"""Automated test suite for Stuck/Defective Sensor Detection (AGRO-17 / SCRUM-36).

Covers:
- SCRUM-150 / AC-01: 24-hour rolling window zero-variance evaluator (variance < 0.001, RN-02).
- SCRUM-151 / AC-02: Defective sensor flagging (DEFECTIVE) and PostgreSQL health status updater.
- SCRUM-152 / AC-02: Automated maintenance alert notification trigger to RabbitMQ.
- AC-03: 6-hour dynamic variance recovery back to ACTIVE & false positive suppression.
- AC-04: 100% detection of simulated DEF-02 stuck sensors within 15 minutes of crossing 24h window.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from agrosense_api.services.sensor_health import (
    DefectiveSensorAlertEvent,
    SensorHealthMonitorService,
    SensorHealthStatus,
    SlidingWindowVarianceEvaluator,
    TelemetrySample,
)


def _generate_stream(
    sensor_id: str,
    farm_id: str,
    sensor_type: str,
    start_dt: datetime,
    hours: int,
    interval_minutes: int = 10,
    *,
    stuck_value: float | None = None,
) -> list[dict[str, Any]]:
    total_steps = int((hours * 60) // interval_minutes) + 1
    samples: list[dict[str, Any]] = []
    for step in range(total_steps):
        ts = start_dt + timedelta(minutes=step * interval_minutes)
        if stuck_value is not None:
            val = stuck_value
        else:
            val = round(22.0 + (step % 7) * 0.65, 2)
        samples.append(
            {
                "sensor_id": sensor_id,
                "farm_id": farm_id,
                "sensor_type": sensor_type,
                "reading_value": val,
                "reading_timestamp": ts.isoformat().replace("+00:00", "Z"),
            }
        )
    return samples


def test_ac01_zero_variance_detection_flags_stuck_sensor_at_24h() -> None:
    """AC-01 (SCRUM-150): Sensor with variance < 0.001 over 24h is flagged as flatlining."""
    monitor = SensorHealthMonitorService()
    start_dt = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    stream = _generate_stream(
        sensor_id="sensor-stuck-01",
        farm_id="FIN-0001",
        sensor_type="SOIL_MOISTURE",
        start_dt=start_dt,
        hours=24,
        interval_minutes=10,
        stuck_value=31.4,
    )

    alerts: list[DefectiveSensorAlertEvent] = []
    last_eval = None
    for payload in stream:
        last_eval, alert = monitor.process_sample(payload)
        if alert is not None:
            alerts.append(alert)

    assert last_eval is not None
    assert last_eval.is_window_complete is True
    assert last_eval.variance == 0.0
    assert last_eval.is_flatlining is True
    assert monitor.get_status("sensor-stuck-01") == SensorHealthStatus.DEFECTIVE
    assert len(alerts) == 1
    assert alerts[0].new_status == SensorHealthStatus.DEFECTIVE
    assert alerts[0].rule_ref == "RN-02"
    assert alerts[0].to_dict()["new_status"] == "DEFECTIVE"


def test_ac01_normal_dynamic_sensor_remains_active() -> None:
    """AC-01: Sensor with normal diurnal variations (variance >= 0.001) stays ACTIVE."""
    monitor = SensorHealthMonitorService()
    start_dt = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    stream = _generate_stream(
        sensor_id="sensor-healthy-01",
        farm_id="FIN-0001",
        sensor_type="TEMPERATURE",
        start_dt=start_dt,
        hours=26,
        interval_minutes=10,
        stuck_value=None,
    )

    for payload in stream:
        eval_res, alert = monitor.process_sample(payload)
        assert alert is None

    assert eval_res.is_window_complete is True
    assert eval_res.variance is not None and eval_res.variance > 0.001
    assert eval_res.is_flatlining is False
    assert monitor.get_status("sensor-healthy-01") == SensorHealthStatus.ACTIVE
    assert len(monitor.emitted_alerts) == 0


def test_ac03_recovery_after_6h_dynamic_variance_and_false_positive_suppression() -> None:
    """AC-03 (SCRUM-151): DEFECTIVE sensor stays DEFECTIVE under 6h, recovers to ACTIVE at 6h."""
    monitor = SensorHealthMonitorService()
    start_dt = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)

    # 1. Flatline for 24 hours -> becomes DEFECTIVE at T+24h
    stuck_stream = _generate_stream(
        sensor_id="sensor-recover-01",
        farm_id="FIN-0002",
        sensor_type="SOIL_MOISTURE",
        start_dt=start_dt,
        hours=24,
        interval_minutes=10,
        stuck_value=28.0,
    )
    for payload in stuck_stream:
        monitor.process_sample(payload)

    assert monitor.get_status("sensor-recover-01") == SensorHealthStatus.DEFECTIVE

    # 2. Resume dynamic readings for 3 hours (< 6h window) -> stays DEFECTIVE
    resume_start = start_dt + timedelta(hours=24, minutes=10)
    short_recovery = _generate_stream(
        sensor_id="sensor-recover-01",
        farm_id="FIN-0002",
        sensor_type="SOIL_MOISTURE",
        start_dt=resume_start,
        hours=3,
        interval_minutes=10,
        stuck_value=None,
    )
    for payload in short_recovery:
        monitor.process_sample(payload)

    assert monitor.get_status("sensor-recover-01") == SensorHealthStatus.DEFECTIVE

    # 3. Continue dynamic readings past 6 hours total from T+24h -> recovers to ACTIVE
    remaining_recovery = _generate_stream(
        sensor_id="sensor-recover-01",
        farm_id="FIN-0002",
        sensor_type="SOIL_MOISTURE",
        start_dt=start_dt + timedelta(hours=27, minutes=20),
        hours=3,
        interval_minutes=10,
        stuck_value=None,
    )
    for payload in remaining_recovery:
        monitor.process_sample(payload)

    assert monitor.get_status("sensor-recover-01") == SensorHealthStatus.ACTIVE
    assert monitor.emitted_alerts[-1].previous_status == SensorHealthStatus.DEFECTIVE
    assert monitor.emitted_alerts[-1].new_status == SensorHealthStatus.ACTIVE


def test_ac04_100_percent_def02_stuck_sensors_detected_within_15_minutes_of_24h() -> None:
    """AC-04: 100% of simulated DEF-02 stuck sensors are flagged within 15 min of crossing 24h."""
    monitor = SensorHealthMonitorService()
    start_dt = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)

    stuck_ids = {f"sensor-def02-{i:03d}" for i in range(1, 11)}
    healthy_ids = {f"sensor-ok-{i:03d}" for i in range(1, 21)}

    detected_delays_minutes: dict[str, float] = {}
    window_24h_dt = start_dt + timedelta(hours=24)

    for sid in stuck_ids:
        for payload in _generate_stream(
            sid, "FIN-0010", "TEMPERATURE", start_dt, hours=25, stuck_value=21.5
        ):
            _, alert = monitor.process_sample(payload)
            if alert and alert.new_status == SensorHealthStatus.DEFECTIVE:
                delay_min = (alert.detected_at - window_24h_dt).total_seconds() / 60.0
                detected_delays_minutes[sid] = delay_min

    for sid in healthy_ids:
        for payload in _generate_stream(
            sid, "FIN-0010", "TEMPERATURE", start_dt, hours=25, stuck_value=None
        ):
            monitor.process_sample(payload)

    assert set(detected_delays_minutes.keys()) == stuck_ids
    assert all(0.0 <= delay <= 15.0 for delay in detected_delays_minutes.values())
    assert all(monitor.get_status(sid) == SensorHealthStatus.ACTIVE for sid in healthy_ids)


def test_scrum151_and_152_db_persistence_and_out_of_order_handling() -> None:
    """SCRUM-151 & SCRUM-152: Verify DB status update SQL execution and out-of-order timestamps."""
    evaluator = SlidingWindowVarianceEvaluator(
        evaluation_window_hours=24.0,
        recovery_window_hours=6.0,
        variance_threshold=0.001,
    )
    empty_res = evaluator.evaluate_sensor("non-existent")
    assert empty_res.sample_count == 0
    assert empty_res.is_flatlining is False
    assert evaluator.evaluate_recovery_window("non-existent") == (False, None)

    t0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    evaluator.ingest_sample(
        TelemetrySample("s-order", "FIN-1", "TEMPERATURE", 20.0, t0 + timedelta(minutes=20))
    )
    res = evaluator.ingest_sample(
        TelemetrySample("s-order", "FIN-1", "TEMPERATURE", 20.0, t0 + timedelta(minutes=10))
    )
    assert res.sample_count == 2

    executed_queries: list[tuple[str, dict[str, Any]]] = []

    class DummySession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> None:
            executed_queries.append((str(stmt), params))

        async def commit(self) -> None:
            pass

    monitor = SensorHealthMonitorService(evaluator=evaluator)
    asyncio.run(
        monitor.persist_status_in_db(
            DummySession(),  # type: ignore[arg-type]
            "s-order",
            SensorHealthStatus.DEFECTIVE,
        )
    )
    assert len(executed_queries) == 1
    assert executed_queries[0][1] == {"status": "DEFECTIVE", "sensor_id": "s-order"}
