"""Automated verification suite for AGRO-20 (SCRUM-39 / EP-06).

Verifies:
- SCRUM-165 (RN-03, CA-03): PySpark 4-day rolling window aggregator for Coffee Rust alerts
  (relative humidity > 85% for >= 6h/day and daily mean temp 21-25°C).
- Satellite Fallback (RN-03): Automatic fallback to NASA POWER and Open-Meteo bronze data
  when IoT field sensors are flagged stuck/defective or missing.
- RN-04 & CA-04: 5-day rolling window aggregator for Water Deficit alerts (soil moisture < 25%)
  with suppression/cancellation when 5-day ahead rainfall forecast is >= 5 mm.
- SCRUM-164: Alert payload integrity hashing (SHA-256) and validation.
- SCRUM-169: Silver feature and alert Parquet writing with Snappy compression, AWS Glue partition
  registration, and idempotent PostgreSQL persistence.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock

import pyarrow.parquet as pq
from agrosense_api.services.agronomic_alerts_spark import (
    AgroclimaticAlertOutputWriter,
    AlertIntegrityHashValidator,
    AlertStatus,
    AlertType,
    DataSourceUsed,
    PySparkRollingWindowAlertEngine,
    SatelliteFallbackNormalizer,
)

from pipelines.spark_jobs.coffee_rust_water_deficit_alerts import (
    run_coffee_rust_and_water_deficit_pipeline,
)


def test_scrum165_coffee_rust_4day_rolling_window_trigger() -> None:
    """Verify RN-03/CA-03: 4 consecutive days with RH > 85% >= 6h & Temp 21-25°C triggers alert."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-001",
            "observation_date": "2026-10-10",
            "high_humidity_hours_gt85": 7,
            "daily_mean_temp_c": 22.5,
            "soil_moisture_mean_pct": 38.0,
            "daily_precip_mm": 5.0,
            "forecast_rain_next_5d_mm": 10.0,
        },
        {
            "farm_id": "FARM-001",
            "observation_date": "2026-10-11",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 23.0,
            "soil_moisture_mean_pct": 36.0,
            "daily_precip_mm": 3.0,
            "forecast_rain_next_5d_mm": 12.0,
        },
        {
            "farm_id": "FARM-001",
            "observation_date": "2026-10-12",
            "high_humidity_hours_gt85": 6,
            "daily_mean_temp_c": 21.5,
            "soil_moisture_mean_pct": 35.0,
            "daily_precip_mm": 0.0,
            "forecast_rain_next_5d_mm": 8.0,
        },
        {
            "farm_id": "FARM-001",
            "observation_date": "2026-10-13",
            "high_humidity_hours_gt85": 9,
            "daily_mean_temp_c": 24.0,
            "soil_moisture_mean_pct": 32.0,
            "daily_precip_mm": 1.0,
            "forecast_rain_next_5d_mm": 15.0,
        },
    ]

    features, alerts = engine.evaluate_rolling_windows(daily_inputs)

    assert len(features) == 4
    assert features[-1].consecutive_rust_days == 4

    rust_alerts = [a for a in alerts if a.alert_type == AlertType.COFFEE_RUST]
    assert len(rust_alerts) == 1
    alert = rust_alerts[0]

    assert alert.farm_id == "FARM-001"
    assert alert.alert_status == AlertStatus.ACTIVE
    assert alert.weight_multiplier == 3
    assert alert.forecast_days_ahead == 5
    assert alert.data_source_used == DataSourceUsed.IOT_SENSOR
    assert alert.evidence_summary["consecutive_days_hr_gt_85"] == 4
    assert alert.evidence_summary["rule_ref"] == "RN-03"
    assert "integrity_hash" in alert.evidence_summary


def test_scrum165_coffee_rust_negative_conditions() -> None:
    """Verify that streaks reset and no alert is triggered if conditions fail on any day."""
    engine = PySparkRollingWindowAlertEngine()

    # Case A: Only 3 days
    three_days = [
        {
            "farm_id": "FARM-002",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 23.0,
            "soil_moisture_mean_pct": 40.0,
        }
        for i in range(3)
    ]
    _, alerts_a = engine.evaluate_rolling_windows(three_days)
    assert not any(a.alert_type == AlertType.COFFEE_RUST for a in alerts_a)

    # Case B: Temp outside 21-25 range on Day 3 (e.g. 26.5°C)
    temp_fail = [
        {
            "farm_id": "FARM-003",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 23.0 if i != 2 else 26.5,
            "soil_moisture_mean_pct": 40.0,
        }
        for i in range(4)
    ]
    _, alerts_b = engine.evaluate_rolling_windows(temp_fail)
    assert not any(a.alert_type == AlertType.COFFEE_RUST for a in alerts_b)

    # Case C: Humidity hours < 6 on Day 2
    humidity_fail = [
        {
            "farm_id": "FARM-004",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8 if i != 1 else 5,
            "daily_mean_temp_c": 22.0,
            "soil_moisture_mean_pct": 40.0,
        }
        for i in range(4)
    ]
    _, alerts_c = engine.evaluate_rolling_windows(humidity_fail)
    assert not any(a.alert_type == AlertType.COFFEE_RUST for a in alerts_c)


def test_scrum165_satellite_fallback_when_iot_sensor_stuck_or_missing() -> None:
    """Verify RN-03: fallback to NASA POWER & Open-Meteo when IoT sensors are stuck/missing."""
    iot_readings = [
        {
            "farm_id": "FARM-SAT-01",
            "sensor_type": "TEMPERATURE",
            "standardized_value": 22.0,
            "is_sensor_stuck_24h": True,  # Stuck / Defective sensor
            "is_valid_for_alerts": False,
            "observation_date": "2026-10-15",
        }
    ]

    nasa_payload = {
        "execution_date": "2026-10-15",
        "records": [
            {
                "farm_id": "FARM-SAT-01",
                "status": "SUCCESS",
                "data": {
                    "properties": {
                        "parameter": {
                            "T2M_MEAN": {"20261015": 23.4},
                            "RH2M": {"20261015": 88.0},
                            "PRECTOTCORR": {"20261015": 12.5},
                        }
                    }
                },
            }
        ],
    }

    open_meteo_payload = {
        "execution_date": "2026-10-15",
        "records": [
            {
                "farm_id": "FARM-SAT-01",
                "status": "SUCCESS",
                "data": {
                    "daily": {
                        "time": ["2026-10-15"],
                        "precipitation_sum": [4.0],
                        "temperature_2m_mean": [23.1],
                    },
                    "hourly": {
                        "time": [f"2026-10-15T{h:02d}:00" for h in range(24)],
                        "relative_humidity_2m": [90.0] * 10 + [70.0] * 14,  # 10 hours > 85%
                    },
                },
            }
        ],
    }

    daily_inputs = SatelliteFallbackNormalizer.build_daily_farm_inputs(
        iot_readings=iot_readings,
        nasa_payloads=nasa_payload,
        open_meteo_payloads=open_meteo_payload,
    )

    assert len(daily_inputs) == 1
    input_row = daily_inputs[0]
    assert input_row["primary_source_used"] == DataSourceUsed.SATELLITE_FALLBACK.value
    assert input_row["high_humidity_hours_gt85"] == 10
    assert 23.0 <= input_row["daily_mean_temp_c"] <= 23.5


def test_scrum165_water_deficit_5day_window_active_trigger() -> None:
    """Verify RN-04: soil moisture < 25% for 5 days with forecast rain < 5mm triggers alert."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-DRY-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 1,
            "daily_mean_temp_c": 27.0,
            "soil_moisture_mean_pct": 21.0 - i,  # all < 25%
            "daily_precip_mm": 0.0,
            "forecast_rain_next_5d_mm": 2.5,  # < 5 mm
        }
        for i in range(5)
    ]

    features, alerts = engine.evaluate_rolling_windows(daily_inputs)

    assert len(features) == 5
    assert features[-1].consecutive_dry_soil_days == 5

    deficit_alerts = [a for a in alerts if a.alert_type == AlertType.WATER_DEFICIT]
    assert len(deficit_alerts) == 1
    alert = deficit_alerts[0]

    assert alert.farm_id == "FARM-DRY-01"
    assert alert.alert_status == AlertStatus.ACTIVE
    assert alert.weight_multiplier == 2
    assert alert.forecast_days_ahead == 5
    assert alert.evidence_summary["consecutive_dry_days"] == 5
    assert alert.evidence_summary["forecast_rain_mm"] == 2.5
    assert alert.evidence_summary["rule_ref"] == "RN-04"


def test_scrum165_water_deficit_suppression_due_to_rain_forecast_ca04() -> None:
    """Verify CA-04: soil moisture < 25% for 5 days but forecast rain >= 5mm suppresses alert."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-RAIN-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 1,
            "daily_mean_temp_c": 26.0,
            "soil_moisture_mean_pct": 20.0,
            "daily_precip_mm": 0.0,
            "forecast_rain_next_5d_mm": 8.5,  # >= 5 mm (CA-04 suppression)
        }
        for i in range(5)
    ]

    _, alerts = engine.evaluate_rolling_windows(daily_inputs)

    deficit_alerts = [a for a in alerts if a.alert_type == AlertType.WATER_DEFICIT]
    assert len(deficit_alerts) == 1
    alert = deficit_alerts[0]

    assert alert.alert_status == AlertStatus.EXPIRED
    assert alert.evidence_summary["action"] == "SUPPRESSED_DUE_TO_RAIN_FORECAST"
    assert alert.evidence_summary["rule_ref"] == "CA-04"
    assert alert.evidence_summary["forecast_rain_mm"] == 8.5


def test_scrum164_alert_integrity_hash_verification() -> None:
    """Verify SCRUM-164: SHA-256 integrity hash verification and tamper detection."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-HASH-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 22.5,
            "soil_moisture_mean_pct": 35.0,
        }
        for i in range(4)
    ]
    _, alerts = engine.evaluate_rolling_windows(daily_inputs)
    assert len(alerts) >= 1
    alert = alerts[0]

    # Verification passes on authentic payload
    assert AlertIntegrityHashValidator.verify_alert_payload(alert) is True

    # Verification fails on tampered evidence
    tampered = alert.to_dict()
    tampered["evidence_summary"]["avg_temp_c"] = 99.9
    assert AlertIntegrityHashValidator.verify_alert_payload(tampered) is False


def test_scrum169_s3_snappy_parquet_and_glue_partition_registration() -> None:
    """Verify SCRUM-169: Silver feature and alert Snappy Parquet generation and Glue sync."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-S3-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 22.5,
            "soil_moisture_mean_pct": 35.0,
            "daily_precip_mm": 2.0,
            "forecast_rain_next_5d_mm": 1.0,
        }
        for i in range(4)
    ]
    features, alerts = engine.evaluate_rolling_windows(daily_inputs)

    # 1. Test Feature Parquet Bytes
    feature_bytes = AgroclimaticAlertOutputWriter.features_to_snappy_parquet_bytes(features)
    pf_feat = pq.ParquetFile(io.BytesIO(feature_bytes))
    assert pf_feat.metadata.num_rows == 4
    assert pf_feat.metadata.row_group(0).column(0).compression == "SNAPPY"

    # 2. Test Alert Parquet Bytes
    alert_bytes = AgroclimaticAlertOutputWriter.alerts_to_snappy_parquet_bytes(alerts)
    pf_alert = pq.ParquetFile(io.BytesIO(alert_bytes))
    assert pf_alert.metadata.num_rows == len(alerts)
    assert pf_alert.metadata.row_group(0).column(0).compression == "SNAPPY"

    # 3. Test S3 and Glue writing
    mock_s3 = MagicMock()
    mock_glue = MagicMock()

    s3_result = AgroclimaticAlertOutputWriter.write_features_and_alerts_to_s3(
        s3_client=mock_s3,
        bucket_name="datalakeagrosense",
        features=features,
        alerts=alerts,
        glue_client=mock_glue,
    )

    assert len(s3_result["feature_parquet_uris"]) == 4
    assert len(s3_result["alert_parquet_uris"]) == 1
    assert mock_s3.put_object.call_count == 5
    assert mock_glue.create_partition.call_count == 4


def test_scrum169_postgresql_idempotent_upsert() -> None:
    """Verify SCRUM-169: Idempotent upsert into PostgreSQL `agronomic_alerts`."""
    engine = PySparkRollingWindowAlertEngine()
    daily_inputs = [
        {
            "farm_id": "FARM-PG-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 8,
            "daily_mean_temp_c": 22.5,
            "soil_moisture_mean_pct": 35.0,
        }
        for i in range(4)
    ]
    _, alerts = engine.evaluate_rolling_windows(daily_inputs)

    mock_session = AsyncMock()
    upserted_count = asyncio.run(
        AgroclimaticAlertOutputWriter.upsert_alerts_to_postgres(
            db_session=mock_session,
            alerts=alerts,
        )
    )

    assert upserted_count == len(alerts)
    assert mock_session.execute.call_count == len(alerts)
    assert mock_session.commit.call_count == 1


def test_end_to_end_spark_pipeline_runner() -> None:
    """Verify end-to-end execution of `run_coffee_rust_and_water_deficit_pipeline`."""
    mock_s3 = MagicMock()
    mock_glue = MagicMock()
    mock_session = AsyncMock()

    daily_records = [
        {
            "farm_id": "FARM-E2E-01",
            "observation_date": f"2026-10-{10 + i}",
            "high_humidity_hours_gt85": 7,
            "daily_mean_temp_c": 23.0,
            "soil_moisture_mean_pct": 20.0,
            "daily_precip_mm": 1.0,
            "forecast_rain_next_5d_mm": 2.0,
        }
        for i in range(5)
    ]

    summary = asyncio.run(
        run_coffee_rust_and_water_deficit_pipeline(
            s3_client=mock_s3,
            glue_client=mock_glue,
            db_session=mock_session,
            iot_readings=daily_records,
        )
    )

    assert summary["status"] == "SUCCESS"
    assert summary["farms_processed"] == 1
    assert summary["silver_features_count"] == 5
    assert summary["alerts_count"] >= 2  # Rust on day 4 & 5, and Deficit on day 5
    assert summary["active_alerts_count"] >= 2
    assert summary["postgres_upserted_count"] == summary["alerts_count"]
    assert len(summary["feature_parquet_uris"]) == 5
