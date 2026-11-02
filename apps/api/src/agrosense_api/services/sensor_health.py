"""AgroSense Café — Automatic Detection of Stuck/Defective Sensors (AGRO-17 / SCRUM-36).

Implements:
- SCRUM-150 (AC-01 / RN-02): Sliding 24-hour window variance evaluator (variance < 0.001).
- SCRUM-151 (AC-02 / AC-03): Defective sensor status flagging (DEFECTIVE), PostgreSQL
  health status updater, and 6-hour dynamic variance recovery back to ACTIVE.
- SCRUM-152 (AC-02): Automated maintenance alert notification trigger to RabbitMQ.
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SensorHealthStatus(StrEnum):
    """Operational health states for IoT field sensors (RN-02)."""

    ACTIVE = "ACTIVE"
    DEFECTIVE = "DEFECTIVE"


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    """Single time-series reading captured by an IoT sensor."""

    sensor_id: str
    farm_id: str
    sensor_type: str
    reading_value: float
    reading_timestamp: datetime

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TelemetrySample:
        raw_ts = payload.get("reading_timestamp") or payload.get("observed_at")
        if isinstance(raw_ts, str):
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        elif isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.now(UTC)

        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        return cls(
            sensor_id=str(payload["sensor_id"]),
            farm_id=str(payload.get("farm_id", "")),
            sensor_type=str(payload.get("sensor_type") or payload.get("metric", "SOIL_MOISTURE")),
            reading_value=float(payload.get("reading_value", payload.get("value", 0.0))),
            reading_timestamp=ts.astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class VarianceEvaluationResult:
    """Result of evaluating a sensor's sliding time window (SCRUM-150)."""

    sensor_id: str
    farm_id: str
    sensor_type: str
    window_hours: float
    sample_count: int
    window_span_hours: float
    variance: float | None
    is_window_complete: bool
    is_flatlining: bool
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class DefectiveSensorAlertEvent:
    """Alert payload emitted when a sensor is flagged DEFECTIVE or recovers (SCRUM-152)."""

    alert_id: UUID
    sensor_id: str
    farm_id: str
    sensor_type: str
    previous_status: SensorHealthStatus
    new_status: SensorHealthStatus
    calculated_variance: float
    window_hours: float
    sample_count: int
    detected_at: datetime
    rule_ref: str = "RN-02"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": str(self.alert_id),
            "sensor_id": self.sensor_id,
            "farm_id": self.farm_id,
            "sensor_type": self.sensor_type,
            "previous_status": self.previous_status.value,
            "new_status": self.new_status.value,
            "calculated_variance": round(self.calculated_variance, 6),
            "window_hours": self.window_hours,
            "sample_count": self.sample_count,
            "detected_at": self.detected_at.isoformat().replace("+00:00", "Z"),
            "rule_ref": self.rule_ref,
        }


class SlidingWindowVarianceEvaluator:
    """Evaluates rolling 24-hour and 6-hour telemetry variance per sensor (SCRUM-150)."""

    MONITORED_TYPES: frozenset[str] = frozenset(
        {"SOIL_MOISTURE", "TEMPERATURE", "soil_moisture", "temperature"}
    )

    def __init__(
        self,
        *,
        evaluation_window_hours: float = 24.0,
        recovery_window_hours: float = 6.0,
        variance_threshold: float = 0.001,
        min_samples_for_evaluation: int = 6,
        min_samples_for_recovery: int = 4,
    ) -> None:
        self.evaluation_window_hours = evaluation_window_hours
        self.recovery_window_hours = recovery_window_hours
        self.variance_threshold = variance_threshold
        self.min_samples_for_evaluation = min_samples_for_evaluation
        self.min_samples_for_recovery = min_samples_for_recovery
        self._windows: dict[str, deque[TelemetrySample]] = {}

    def ingest_sample(self, sample: TelemetrySample) -> VarianceEvaluationResult:
        """Append sample in chronological order, evict samples older than 24h, and evaluate."""
        buf = self._windows.setdefault(sample.sensor_id, deque())

        if buf and sample.reading_timestamp < buf[-1].reading_timestamp:
            ordered = sorted([*buf, sample], key=lambda item: item.reading_timestamp)
            buf.clear()
            buf.extend(ordered)
        else:
            buf.append(sample)

        latest_ts = buf[-1].reading_timestamp
        cutoff_ts = latest_ts - timedelta(hours=self.evaluation_window_hours)

        while len(buf) > 1 and buf[0].reading_timestamp < cutoff_ts:
            if buf[1].reading_timestamp <= cutoff_ts:
                buf.popleft()
            else:
                break

        return self.evaluate_sensor(sample.sensor_id, reference_time=latest_ts)

    def evaluate_sensor(
        self,
        sensor_id: str,
        *,
        reference_time: datetime | None = None,
    ) -> VarianceEvaluationResult:
        """Compute rolling 24-hour variance for the given sensor (AC-01)."""
        buf = self._windows.get(sensor_id)
        if not buf:
            now = reference_time or datetime.now(UTC)
            return VarianceEvaluationResult(
                sensor_id=sensor_id,
                farm_id="",
                sensor_type="UNKNOWN",
                window_hours=self.evaluation_window_hours,
                sample_count=0,
                window_span_hours=0.0,
                variance=None,
                is_window_complete=False,
                is_flatlining=False,
                evaluated_at=now,
            )

        latest = buf[-1]
        earliest = buf[0]
        elapsed_sec = (latest.reading_timestamp - earliest.reading_timestamp).total_seconds()
        span_hours = elapsed_sec / 3600.0
        sample_count = len(buf)

        is_monitored = latest.sensor_type in self.MONITORED_TYPES
        is_window_complete = (
            span_hours >= self.evaluation_window_hours
            and sample_count >= self.min_samples_for_evaluation
        )

        values = [item.reading_value for item in buf]
        variance = statistics.pvariance(values) if sample_count >= 2 else None
        is_flatlining = bool(
            is_monitored
            and is_window_complete
            and variance is not None
            and variance < self.variance_threshold
        )

        return VarianceEvaluationResult(
            sensor_id=sensor_id,
            farm_id=latest.farm_id,
            sensor_type=latest.sensor_type,
            window_hours=self.evaluation_window_hours,
            sample_count=sample_count,
            window_span_hours=round(span_hours, 4),
            variance=variance,
            is_window_complete=is_window_complete,
            is_flatlining=is_flatlining,
            evaluated_at=latest.reading_timestamp,
        )

    def evaluate_recovery_window(
        self,
        sensor_id: str,
        *,
        defective_since: datetime | None = None,
    ) -> tuple[bool, float | None]:
        """Evaluate whether subsequent 6h telemetry shows normal variance (AC-03)."""
        buf = self._windows.get(sensor_id)
        if not buf:
            return False, None

        latest_ts = buf[-1].reading_timestamp
        recovery_cutoff = latest_ts - timedelta(hours=self.recovery_window_hours)
        if defective_since is not None:
            effective_start = max(recovery_cutoff, defective_since)
        else:
            effective_start = recovery_cutoff
        recent_samples = [s for s in buf if s.reading_timestamp >= effective_start]

        if len(recent_samples) < self.min_samples_for_recovery:
            return False, None

        span_hours = (
            recent_samples[-1].reading_timestamp - recent_samples[0].reading_timestamp
        ).total_seconds() / 3600.0

        if span_hours < self.recovery_window_hours:
            return False, None

        variance = statistics.pvariance(s.reading_value for s in recent_samples)
        has_recovered = variance >= self.variance_threshold
        return has_recovered, variance


@dataclass
class SensorHealthMonitorService:
    """Orchestrates stuck sensor detection, DB persistence, and alerts (SCRUM-151, SCRUM-152)."""

    evaluator: SlidingWindowVarianceEvaluator = field(
        default_factory=SlidingWindowVarianceEvaluator
    )
    maintenance_queue: str = "agrosense.maintenance.alerts"
    sensor_statuses: dict[str, SensorHealthStatus] = field(default_factory=dict)
    defective_since: dict[str, datetime] = field(default_factory=dict)
    emitted_alerts: list[DefectiveSensorAlertEvent] = field(default_factory=list)
    db_status_updates: list[dict[str, Any]] = field(default_factory=list)
    alert_callbacks: list[Callable[[DefectiveSensorAlertEvent], None]] = field(default_factory=list)

    def get_status(self, sensor_id: str) -> SensorHealthStatus:
        """Return current health status of a sensor (defaults to ACTIVE)."""
        return self.sensor_statuses.get(sensor_id, SensorHealthStatus.ACTIVE)

    def process_sample(
        self,
        sample: TelemetrySample | dict[str, Any],
    ) -> tuple[VarianceEvaluationResult, DefectiveSensorAlertEvent | None]:
        """Process incoming telemetry reading, update status if needed, and trigger alerts."""
        parsed = (
            sample if isinstance(sample, TelemetrySample) else TelemetrySample.from_payload(sample)
        )
        current_status = self.get_status(parsed.sensor_id)
        eval_result = self.evaluator.ingest_sample(parsed)

        # Case 1: ACTIVE sensor flatlines over 24-hour window -> DEFECTIVE (AC-01, AC-02)
        if current_status == SensorHealthStatus.ACTIVE and eval_result.is_flatlining:
            self.defective_since[parsed.sensor_id] = parsed.reading_timestamp
            alert = self._transition_status(
                sensor_id=parsed.sensor_id,
                farm_id=parsed.farm_id,
                sensor_type=parsed.sensor_type,
                previous_status=SensorHealthStatus.ACTIVE,
                new_status=SensorHealthStatus.DEFECTIVE,
                calculated_variance=eval_result.variance or 0.0,
                window_hours=self.evaluator.evaluation_window_hours,
                sample_count=eval_result.sample_count,
                detected_at=parsed.reading_timestamp,
            )
            return eval_result, alert

        # Case 2: DEFECTIVE sensor resumes normal variance over 6-hour window -> ACTIVE (AC-03)
        if current_status == SensorHealthStatus.DEFECTIVE:
            recovered, rec_variance = self.evaluator.evaluate_recovery_window(
                parsed.sensor_id,
                defective_since=self.defective_since.get(parsed.sensor_id),
            )
            if recovered and rec_variance is not None:
                self.defective_since.pop(parsed.sensor_id, None)
                alert = self._transition_status(
                    sensor_id=parsed.sensor_id,
                    farm_id=parsed.farm_id,
                    sensor_type=parsed.sensor_type,
                    previous_status=SensorHealthStatus.DEFECTIVE,
                    new_status=SensorHealthStatus.ACTIVE,
                    calculated_variance=rec_variance,
                    window_hours=self.evaluator.recovery_window_hours,
                    sample_count=eval_result.sample_count,
                    detected_at=parsed.reading_timestamp,
                )
                return eval_result, alert

        self.sensor_statuses.setdefault(parsed.sensor_id, current_status)
        return eval_result, None

    def _transition_status(
        self,
        *,
        sensor_id: str,
        farm_id: str,
        sensor_type: str,
        previous_status: SensorHealthStatus,
        new_status: SensorHealthStatus,
        calculated_variance: float,
        window_hours: float,
        sample_count: int,
        detected_at: datetime,
    ) -> DefectiveSensorAlertEvent:
        self.sensor_statuses[sensor_id] = new_status
        self.db_status_updates.append(
            {
                "sensor_id": sensor_id,
                "farm_id": farm_id,
                "status": new_status.value,
                "updated_at": detected_at.isoformat().replace("+00:00", "Z"),
            }
        )

        event = DefectiveSensorAlertEvent(
            alert_id=uuid4(),
            sensor_id=sensor_id,
            farm_id=farm_id,
            sensor_type=sensor_type,
            previous_status=previous_status,
            new_status=new_status,
            calculated_variance=calculated_variance,
            window_hours=window_hours,
            sample_count=sample_count,
            detected_at=detected_at,
        )
        self.emitted_alerts.append(event)

        for callback in self.alert_callbacks:
            callback(event)

        logger.info(
            "Sensor %s (farm=%s, type=%s) transitioned %s -> %s (variance=%.6f, window=%.1fh)",
            sensor_id,
            farm_id,
            sensor_type,
            previous_status.value,
            new_status.value,
            calculated_variance,
            window_hours,
        )
        return event

    async def persist_status_in_db(
        self,
        session: AsyncSession,
        sensor_id: str,
        new_status: SensorHealthStatus,
    ) -> None:
        """Update sensor health status in PostgreSQL (SCRUM-151 / AC-02 & AC-03)."""
        stmt = text(
            "UPDATE iot_sensors "
            "SET status = :status, health_status = :status "
            "WHERE sensor_id = :sensor_id"
        )
        await session.execute(stmt, {"status": new_status.value, "sensor_id": sensor_id})
        await session.commit()

    async def publish_maintenance_alert(
        self,
        event: DefectiveSensorAlertEvent,
        rabbitmq_url: str,
    ) -> None:
        """Publish defective sensor alert event to RabbitMQ maintenance queue (SCRUM-152)."""
        import aio_pika

        connection = await aio_pika.connect_robust(rabbitmq_url)
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            queue = await channel.declare_queue(self.maintenance_queue, durable=True)
            message = aio_pika.Message(
                body=json.dumps(event.to_dict()).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(event.alert_id),
            )
            await channel.default_exchange.publish(message, routing_key=queue.name)
