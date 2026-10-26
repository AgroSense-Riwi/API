"""AgroSense Café — PySpark Job: Soil NPK Normalization & Recommendations (AGRO-22 / SCRUM-41).

Executes:
1. Heterogeneous soil unit normalization to mg/kg (SCRUM-170, CA-06).
2. Kaggle coffee benchmark comparison matrix matching (SCRUM-171).
3. Tailored fertilizer recommendation generation per farm plot (SCRUM-172, RN-07).
4. Silver Parquet export + Glue registration and PostgreSQL idempotent persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agrosense_api.services.soil_recommendations_spark import (
    CropNutrientBenchmark,
    FertilizerRecommendation,
    NormalizedSoilAnalysis,
    SoilFertilizationRecommendationEngine,
    SoilLabUnitNormalizer,
    SoilOutputWriter,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def run_soil_recommendations_pipeline(
    raw_analyses: list[dict[str, Any]],
    kaggle_csv_path: str | Path | None = None,
    s3_client: Any | None = None,
    glue_client: Any | None = None,
    db_session: AsyncSession | None = None,
    bucket_name: str = "datalakeagrosense",
    database_name: str = "agrosense_datalake",
) -> dict[str, Any]:
    """Execute end-to-end Soil Normalization and Fertilizer Recommendation Spark job."""
    # 1. Load benchmark from Kaggle CSV if available, or fall back to defaults (SCRUM-171)
    benchmark = (
        CropNutrientBenchmark.from_kaggle_csv(kaggle_csv_path)
        if kaggle_csv_path
        else CropNutrientBenchmark()
    )
    engine = SoilFertilizationRecommendationEngine(benchmark=benchmark)

    # 2. Normalize laboratory units to mg/kg (SCRUM-170, CA-06)
    normalized: list[NormalizedSoilAnalysis] = [
        SoilLabUnitNormalizer.normalize_analysis(r) for r in raw_analyses
    ]

    # 3. Generate tailored recommendations (SCRUM-172, RN-07)
    recommendations: list[FertilizerRecommendation] = [
        engine.generate_recommendation(a) for a in normalized
    ]

    # 4. Export Parquet to S3 Silver layer & Glue catalog
    s3_uri = None
    if s3_client is not None and normalized:
        sample_date = normalized[0].sample_date
        s3_uri = SoilOutputWriter.write_analyses_to_s3(
            s3_client=s3_client,
            bucket_name=bucket_name,
            analyses=normalized,
            partition_date=sample_date,
            glue_client=glue_client,
            database_name=database_name,
        )

    # 5. Persist to PostgreSQL
    upserted_analyses, upserted_recs = 0, 0
    if db_session is not None:
        (
            upserted_analyses,
            upserted_recs,
        ) = await SoilOutputWriter.upsert_soil_and_recommendations_to_postgres(
            db_session=db_session,
            analyses=normalized,
            recommendations=recommendations,
        )

    return {
        "status": "SUCCESS",
        "analyses_normalized_count": len(normalized),
        "recommendations_generated_count": len(recommendations),
        "s3_parquet_uri": s3_uri,
        "postgres_upserted_analyses": upserted_analyses,
        "postgres_upserted_recommendations": upserted_recs,
        "recommendations": [r.to_dict() for r in recommendations],
    }
