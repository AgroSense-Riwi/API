"""AgroSense Café — PySpark Job: Agronomic Visit Prioritization (AGRO-21 / SCRUM-40).

Executes:
1. 72-hour rainfall accumulation and EXTREME_RAIN alert generation (RN-05).
2. Multi-criteria priority ranking computation with tie-breaking (RN-06, CA-05).
3. Export to PostgreSQL `agronomist_field_visits` and S3 Refined Parquet (SCRUM-175).
"""

from __future__ import annotations

from typing import Any

from agrosense_api.services.agronomic_alerts_spark import AgronomicAlertPayload
from agrosense_api.services.visit_ranking_spark import (
    AgronomicVisitRankingEngine,
    ExcessRainfallEvaluator,
    PrioritizedVisitRecord,
    VisitRankingOutputWriter,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def run_visit_ranking_priority_pipeline(
    farms: list[dict[str, Any]],
    alerts: list[AgronomicAlertPayload | dict[str, Any]],
    calculation_date: str,
    s3_client: Any | None = None,
    db_session: AsyncSession | None = None,
    bucket_name: str = "datalakeagrosense",
    agronomist_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Execute end-to-end Agronomic Visit Prioritization Spark job."""
    combined_alerts = list(alerts)

    # 1. Evaluate excess rainfall for farms if 72h accumulation is supplied
    for f in farms:
        precip_72h = float(f.get("precip_accum_72h_mm", 0.0))
        if precip_72h > ExcessRainfallEvaluator.THRESHOLD_MM:
            rain_alert = ExcessRainfallEvaluator.evaluate_72h_rainfall(
                farm_id=str(f["farm_id"]),
                observation_date=calculation_date,
                precip_accum_72h_mm=precip_72h,
            )
            if rain_alert:
                combined_alerts.append(rain_alert)

    # 2. Rank farms and generate prioritized visits (RN-06, CA-05)
    visits: list[PrioritizedVisitRecord] = AgronomicVisitRankingEngine.generate_prioritized_visits(
        farms=farms,
        alerts=combined_alerts,
        calculation_date=calculation_date,
        agronomist_ids=agronomist_ids,
    )

    # 3. Export to S3 Refined layer Parquet (SCRUM-175)
    s3_uri = None
    if s3_client is not None:
        s3_uri = VisitRankingOutputWriter.write_visits_to_s3(
            s3_client=s3_client,
            bucket_name=bucket_name,
            visits=visits,
            calculation_date=calculation_date,
        )

    # 4. Upsert to PostgreSQL agronomist_field_visits (SCRUM-175)
    upserted_count = 0
    if db_session is not None:
        upserted_count = await VisitRankingOutputWriter.upsert_visits_to_postgres(
            db_session=db_session,
            visits=visits,
        )

    return {
        "status": "SUCCESS",
        "calculation_date": calculation_date,
        "farms_evaluated": len(farms),
        "visits_ranked": len(visits),
        "s3_parquet_uri": s3_uri,
        "postgres_upserted_count": upserted_count,
        "top_priority_score": visits[0].priority_score if visits else 0,
        "visits": [v.to_dict() for v in visits],
    }
