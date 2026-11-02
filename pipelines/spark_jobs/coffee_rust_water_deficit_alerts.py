"""AgroSense Café — PySpark Job: Coffee Rust & Water Deficit Alerts (AGRO-20 / SCRUM-39).

Executes:
1. Multi-source normalization of IoT telemetry + NASA POWER & Open-Meteo fallback (RN-03).
2. Distributed 4-day rolling window evaluation for Coffee Rust alerts (RN-03, CA-03).
3. 5-day rolling window evaluation for Water Deficit alerts + rain suppression (RN-04, CA-04).
4. Silver feature and Refined alert Parquet generation with Snappy compression + Glue registration.
5. Idempotent PostgreSQL persistence to `agronomic_alerts` (SCRUM-169).
"""

from __future__ import annotations

from typing import Any

from agrosense_api.services.agronomic_alerts_spark import (
    AgroclimaticAlertOutputWriter,
    PySparkRollingWindowAlertEngine,
    RollingWindowThresholds,
    SatelliteFallbackNormalizer,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def run_coffee_rust_and_water_deficit_pipeline(
    s3_client: Any | None,
    glue_client: Any | None = None,
    db_session: AsyncSession | None = None,
    iot_readings: list[dict[str, Any]] | None = None,
    nasa_payloads: list[dict[str, Any]] | dict[str, Any] | None = None,
    open_meteo_payloads: list[dict[str, Any]] | dict[str, Any] | None = None,
    bucket_name: str = "datalakeagrosense",
    database_name: str = "agrosense_datalake",
    thresholds: RollingWindowThresholds | None = None,
    generated_at_override: str | None = None,
) -> dict[str, Any]:
    """Execute end-to-end Coffee Rust and Water Deficit PySpark batch processing pipeline."""
    raw_iot = iot_readings or []
    engine = PySparkRollingWindowAlertEngine(thresholds=thresholds)

    # 1. Normalize multi-source data with satellite fallback (RN-03)
    daily_inputs = SatelliteFallbackNormalizer.build_daily_farm_inputs(
        iot_readings=raw_iot,
        nasa_payloads=nasa_payloads,
        open_meteo_payloads=open_meteo_payloads,
    )

    # 2. Evaluate 4-day and 5-day rolling windows (RN-03, RN-04, CA-03, CA-04)
    features, alerts = engine.evaluate_rolling_windows(
        daily_inputs=daily_inputs,
        generated_at_override=generated_at_override,
    )

    # 3. Write Parquet partitions to S3 and register in Glue (SCRUM-169)
    uploaded_feature_uris: list[str] = []
    uploaded_alert_uris: list[str] = []
    if s3_client is not None:
        s3_results = AgroclimaticAlertOutputWriter.write_features_and_alerts_to_s3(
            s3_client=s3_client,
            bucket_name=bucket_name,
            features=features,
            alerts=alerts,
            glue_client=glue_client,
            database_name=database_name,
        )
        uploaded_feature_uris = s3_results.get("feature_parquet_uris", [])
        uploaded_alert_uris = s3_results.get("alert_parquet_uris", [])

    # 4. Upsert alerts into PostgreSQL (SCRUM-169)
    postgres_upserted = 0
    if db_session is not None:
        postgres_upserted = await AgroclimaticAlertOutputWriter.upsert_alerts_to_postgres(
            db_session=db_session,
            alerts=alerts,
        )

    return {
        "status": "SUCCESS",
        "farms_processed": len({f.farm_id for f in features}),
        "silver_features_count": len(features),
        "alerts_count": len(alerts),
        "active_alerts_count": len([a for a in alerts if a.alert_status.value == "ACTIVE"]),
        "suppressed_alerts_count": len([a for a in alerts if a.alert_status.value == "EXPIRED"]),
        "feature_parquet_uris": uploaded_feature_uris,
        "alert_parquet_uris": uploaded_alert_uris,
        "postgres_upserted_count": postgres_upserted,
        "alerts": [a.to_dict() for a in alerts],
    }
