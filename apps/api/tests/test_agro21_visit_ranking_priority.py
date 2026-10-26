"""Automated verification suite for AGRO-21 (SCRUM-40 / EP-06).

Verifies:
- SCRUM-173 (RN-05): 72-hour cumulative precipitation > 120 mm triggers EXTREME_RAIN alert.
- SCRUM-174 (RN-06, CA-05): Visit priority score (3*Rust + 2*Deficit + 1*Rain) and tie-breaking
  by cultivated area in hectares.
- SCRUM-175: PostgreSQL `agronomist_field_visits` persistence and S3 Refined Parquet export.
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock

import pyarrow.parquet as pq
from agrosense_api.services.agronomic_alerts_spark import (
    AlertStatus,
    AlertType,
)
from agrosense_api.services.visit_ranking_spark import (
    AgronomicVisitRankingEngine,
    ExcessRainfallEvaluator,
    VisitRankingOutputWriter,
)

from pipelines.spark_jobs.visit_ranking_priority import (
    run_visit_ranking_priority_pipeline,
)


def test_scrum173_72h_rainfall_excess_alert_trigger() -> None:
    """Verify RN-05: 72h accumulated rain > 120 mm triggers EXTREME_RAIN alert."""
    alert = ExcessRainfallEvaluator.evaluate_72h_rainfall(
        farm_id="FARM-RAIN-01",
        observation_date="2026-10-20",
        precip_accum_72h_mm=135.5,
    )
    assert alert is not None
    assert alert.alert_type == AlertType.EXTREME_RAIN
    assert alert.weight_multiplier == 1
    assert alert.forecast_days_ahead == 3
    assert alert.alert_status == AlertStatus.ACTIVE
    assert alert.evidence_summary["accumulated_rain_72h_mm"] == 135.5
    assert alert.evidence_summary["threshold_mm"] == 120.0
    assert "integrity_hash" in alert.evidence_summary

    # No alert when <= 120 mm
    no_alert = ExcessRainfallEvaluator.evaluate_72h_rainfall(
        farm_id="FARM-RAIN-02",
        observation_date="2026-10-20",
        precip_accum_72h_mm=119.9,
    )
    assert no_alert is None


def test_scrum174_visit_priority_scoring_and_tie_breaking_by_area() -> None:
    """Verify RN-06 / CA-05: Priority score formula and tie-breaking by cultivated area."""
    farms = [
        {"farm_id": "FARM-A", "cultivated_area_ha": 4.5},
        {"farm_id": "FARM-B", "cultivated_area_ha": 12.0},  # Same score as A, but larger area
        {"farm_id": "FARM-C", "cultivated_area_ha": 2.0},
    ]

    alerts = [
        # FARM-A has 1 Rust (3 pts) + 1 Deficit (2 pts) = 5 pts
        {
            "farm_id": "FARM-A",
            "alert_type": AlertType.COFFEE_RUST.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        {
            "farm_id": "FARM-A",
            "alert_type": AlertType.WATER_DEFICIT.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        # FARM-B has 1 Rust (3 pts) + 2 Rain (2 pts) = 5 pts (Tied with FARM-A)
        {
            "farm_id": "FARM-B",
            "alert_type": AlertType.COFFEE_RUST.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        {
            "farm_id": "FARM-B",
            "alert_type": AlertType.EXTREME_RAIN.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        {
            "farm_id": "FARM-B",
            "alert_type": AlertType.EXTREME_RAIN.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        # FARM-C has 2 Rust (6 pts) = 6 pts (Highest score)
        {
            "farm_id": "FARM-C",
            "alert_type": AlertType.COFFEE_RUST.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
        {
            "farm_id": "FARM-C",
            "alert_type": AlertType.COFFEE_RUST.value,
            "alert_status": AlertStatus.ACTIVE.value,
        },
    ]

    visits = AgronomicVisitRankingEngine.generate_prioritized_visits(
        farms=farms,
        alerts=alerts,
        calculation_date="2026-10-20",
    )

    assert len(visits) == 3
    # FARM-C should be 1st (score 6)
    assert visits[0].farm_id == "FARM-C"
    assert visits[0].priority_score == 6

    # FARM-B should be 2nd (score 5, area 12.0 ha > FARM-A area 4.5 ha)
    assert visits[1].farm_id == "FARM-B"
    assert visits[1].priority_score == 5
    assert visits[1].tie_breaker_area_ha == 12.0

    # FARM-A should be 3rd (score 5, area 4.5 ha)
    assert visits[2].farm_id == "FARM-A"
    assert visits[2].priority_score == 5
    assert visits[2].tie_breaker_area_ha == 4.5


def test_scrum175_visits_s3_parquet_snappy_and_postgres_export() -> None:
    """Verify SCRUM-175: Snappy Parquet generation and PostgreSQL upsert."""
    farms = [{"farm_id": "FARM-EXP-01", "cultivated_area_ha": 8.0}]
    alerts = [
        {
            "farm_id": "FARM-EXP-01",
            "alert_type": AlertType.COFFEE_RUST.value,
            "alert_status": AlertStatus.ACTIVE.value,
        }
    ]

    visits = AgronomicVisitRankingEngine.generate_prioritized_visits(
        farms=farms,
        alerts=alerts,
        calculation_date="2026-10-20",
    )

    # 1. Test Parquet Bytes & Snappy compression
    pbytes = VisitRankingOutputWriter.visits_to_snappy_parquet_bytes(visits)
    pf = pq.ParquetFile(io.BytesIO(pbytes))
    assert pf.metadata.num_rows == 1
    assert pf.metadata.row_group(0).column(0).compression == "SNAPPY"

    # 2. Test S3 writing
    mock_s3 = MagicMock()
    s3_uri = VisitRankingOutputWriter.write_visits_to_s3(
        s3_client=mock_s3,
        bucket_name="datalakeagrosense",
        visits=visits,
        calculation_date="2026-10-20",
    )
    assert "refined/fact_alerts_and_visits/dt=2026-10-20" in s3_uri
    mock_s3.put_object.assert_called_once()

    # 3. Test PostgreSQL upsert
    mock_session = AsyncMock()
    upserted = asyncio.run(
        VisitRankingOutputWriter.upsert_visits_to_postgres(
            db_session=mock_session,
            visits=visits,
        )
    )
    assert upserted == 1
    assert mock_session.execute.call_count == 1
    assert mock_session.commit.call_count == 1


def test_end_to_end_visit_ranking_pipeline() -> None:
    """Verify end-to-end execution of `run_visit_ranking_priority_pipeline`."""
    farms = [
        {
            "farm_id": "FARM-E2E-A",
            "cultivated_area_ha": 6.0,
            "precip_accum_72h_mm": 130.0,  # Will trigger EXTREME_RAIN (RN-05)
        },
        {
            "farm_id": "FARM-E2E-B",
            "cultivated_area_ha": 10.0,
            "precip_accum_72h_mm": 10.0,
        },
    ]

    mock_s3 = MagicMock()
    mock_session = AsyncMock()

    summary = asyncio.run(
        run_visit_ranking_priority_pipeline(
            farms=farms,
            alerts=[],
            calculation_date="2026-10-20",
            s3_client=mock_s3,
            db_session=mock_session,
        )
    )

    assert summary["status"] == "SUCCESS"
    assert summary["visits_ranked"] == 2
    # FARM-E2E-A received EXTREME_RAIN alert, priority score = 1
    assert summary["top_priority_score"] == 1
    assert summary["visits"][0]["farm_id"] == "FARM-E2E-A"
    assert summary["postgres_upserted_count"] == 2
