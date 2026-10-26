"""Automated verification suite for AGRO-22 (SCRUM-41 / EP-06).

Verifies:
- SCRUM-170 (CA-06): Soil laboratory unit normalization (ppm, mg/kg, g/kg, %) into canonical mg/kg.
  Guarantees identical fertilizer recommendations whether input is reported in ppm or mg/kg.
- SCRUM-171: Soil nutrient vs Kaggle coffee crop benchmark comparison matrix.
- SCRUM-172 (RN-07): Tailored NPK fertilization recommendations, over-fertilization warning,
  and fertilizer savings calculation (~20%).
- Parquet Silver export with Snappy compression, AWS Glue sync, and PostgreSQL persistence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agrosense_api.services.soil_recommendations_spark import (
    CropNutrientBenchmark,
    NutrientEvaluation,
    SoilFertilizationRecommendationEngine,
    SoilLabUnitNormalizer,
    SoilPhEvaluation,
)

from pipelines.spark_jobs.soil_recommendations_spark import (
    run_soil_recommendations_pipeline,
)


def test_scrum170_ca06_soil_unit_normalization_invariance() -> None:
    """Verify CA-06: Identical recommendations produced whether input is in ppm or mg/kg."""
    engine = SoilFertilizationRecommendationEngine()

    raw_mg_kg = {
        "analysis_id": "00000000-0000-0000-0000-000000000001",
        "farm_id": "FARM-SOIL-01",
        "raw_unit_reported": "mg/kg",
        "n_raw_value": 70.0,
        "p_raw_value": 20.0,
        "k_raw_value": 28.0,
        "ph_value": 5.8,
        "sample_date": "2026-10-15",
    }

    raw_ppm = {
        "analysis_id": "00000000-0000-0000-0000-000000000002",
        "farm_id": "FARM-SOIL-01",
        "raw_unit_reported": "ppm",
        "n_raw_value": 70.0,  # 70 ppm = 70 mg/kg
        "p_raw_value": 20.0,
        "k_raw_value": 28.0,
        "ph_value": 5.8,
        "sample_date": "2026-10-15",
    }

    raw_g_kg = {
        "analysis_id": "00000000-0000-0000-0000-000000000003",
        "farm_id": "FARM-SOIL-01",
        "raw_unit_reported": "g/kg",
        "n_raw_value": 0.07,  # 0.07 g/kg = 70 mg/kg
        "p_raw_value": 0.02,  # 0.02 g/kg = 20 mg/kg
        "k_raw_value": 0.028,  # 0.028 g/kg = 28 mg/kg
        "ph_value": 5.8,
        "sample_date": "2026-10-15",
    }

    norm_mg_kg = SoilLabUnitNormalizer.normalize_analysis(raw_mg_kg)
    norm_ppm = SoilLabUnitNormalizer.normalize_analysis(raw_ppm)
    norm_g_kg = SoilLabUnitNormalizer.normalize_analysis(raw_g_kg)

    # Invariant: Normalized mg/kg values must be strictly equal
    assert norm_mg_kg.nitrogen_mg_kg == norm_ppm.nitrogen_mg_kg == norm_g_kg.nitrogen_mg_kg == 70.0
    assert (
        norm_mg_kg.phosphorus_mg_kg
        == norm_ppm.phosphorus_mg_kg
        == norm_g_kg.phosphorus_mg_kg
        == 20.0
    )
    assert norm_mg_kg.potassium_mg_kg == 28.0
    assert norm_ppm.potassium_mg_kg == 28.0
    assert norm_g_kg.potassium_mg_kg == 28.0

    # Invariant: Recommendations must be identical
    rec_mg = engine.generate_recommendation(norm_mg_kg)
    rec_ppm = engine.generate_recommendation(norm_ppm)
    rec_g = engine.generate_recommendation(norm_g_kg)

    assert rec_mg.recommended_n_kg_ha == rec_ppm.recommended_n_kg_ha == rec_g.recommended_n_kg_ha
    assert rec_mg.recommended_p_kg_ha == rec_ppm.recommended_p_kg_ha == rec_g.recommended_p_kg_ha
    assert rec_mg.recommended_k_kg_ha == rec_ppm.recommended_k_kg_ha == rec_g.recommended_k_kg_ha
    assert rec_mg.apply_fertilizer == rec_ppm.apply_fertilizer == rec_g.apply_fertilizer is True


def test_scrum171_kaggle_crop_benchmark_loader() -> None:
    """Verify SCRUM-171: Benchmark comparison against Kaggle coffee dataset."""
    kaggle_path = Path(
        r"C:\Users\Cohorte 5\Documents\Keiner\Agrosense\Agrosense\Datasets\Crop_recommendation.csv"
    )
    benchmark = CropNutrientBenchmark.from_kaggle_csv(kaggle_path)

    assert benchmark.crop_label == "coffee"
    assert benchmark.n_min_mg_kg == 80.0
    assert benchmark.n_max_mg_kg == 120.0
    assert benchmark.p_min_mg_kg == 15.0
    assert benchmark.p_max_mg_kg == 40.0
    assert benchmark.k_min_mg_kg == 25.0
    assert benchmark.k_max_mg_kg == 35.0


def test_scrum172_excess_nutrient_warning_and_dosage_calculation() -> None:
    """Verify RN-07 / SCRUM-172: Over-fertilization prevention and tailored dosages."""
    engine = SoilFertilizationRecommendationEngine()

    # Case A: Deficient soil (N=50 < 80, P=10 < 15, K=20 < 25)
    def_raw = {
        "farm_id": "FARM-DEF-01",
        "raw_unit_reported": "mg/kg",
        "n_raw_value": 50.0,
        "p_raw_value": 10.0,
        "k_raw_value": 20.0,
        "ph_value": 5.2,
    }
    norm_def = SoilLabUnitNormalizer.normalize_analysis(def_raw)
    rec_def = engine.generate_recommendation(norm_def)

    assert rec_def.apply_fertilizer is True
    assert rec_def.n_evaluation == NutrientEvaluation.DEFICIENT
    assert rec_def.p_evaluation == NutrientEvaluation.DEFICIENT
    assert rec_def.k_evaluation == NutrientEvaluation.DEFICIENT
    assert rec_def.ph_evaluation == SoilPhEvaluation.ACIDIC
    # Dosage: N = 180 - (50 * 1.2) = 120.0 kg/ha
    assert rec_def.recommended_n_kg_ha == 120.0
    # Dosage: P = 60 - (10 * 0.8) = 52.0 kg/ha
    assert rec_def.recommended_p_kg_ha == 52.0
    # Lime amendment: (6.0 - 5.2) * 500 = 400.0 kg/ha
    assert rec_def.lime_amendment_kg_ha == 400.0
    assert rec_def.fertilizer_saved_pct >= 20.0

    # Case B: Excessive Nitrogen (N=150 > 120 max) -> Do Not Apply warning
    excess_raw = {
        "farm_id": "FARM-EXCESS-01",
        "raw_unit_reported": "mg/kg",
        "n_raw_value": 150.0,
        "p_raw_value": 25.0,
        "k_raw_value": 30.0,
        "ph_value": 6.2,
    }
    norm_excess = SoilLabUnitNormalizer.normalize_analysis(excess_raw)
    rec_excess = engine.generate_recommendation(norm_excess)

    assert rec_excess.apply_fertilizer is False
    assert rec_excess.n_evaluation == NutrientEvaluation.EXCESS_WARNING
    assert rec_excess.recommended_n_kg_ha == 0.0
    assert rec_excess.recommended_p_kg_ha == 0.0


def test_soil_s3_parquet_and_postgres_export() -> None:
    """Verify Snappy Parquet generation and PostgreSQL persistence for soil pipeline."""
    mock_s3 = MagicMock()
    mock_glue = MagicMock()
    mock_session = AsyncMock()

    kaggle_path = Path(
        r"C:\Users\Cohorte 5\Documents\Keiner\Agrosense\Agrosense\Datasets\Crop_recommendation.csv"
    )
    raw_analyses = [
        {
            "farm_id": "FARM-S3-SOIL",
            "raw_unit_reported": "ppm",
            "n_raw_value": 90.0,
            "p_raw_value": 25.0,
            "k_raw_value": 30.0,
            "ph_value": 6.0,
            "sample_date": "2026-10-15",
        }
    ]

    summary = asyncio.run(
        run_soil_recommendations_pipeline(
            raw_analyses=raw_analyses,
            kaggle_csv_path=kaggle_path,
            s3_client=mock_s3,
            glue_client=mock_glue,
            db_session=mock_session,
        )
    )

    assert summary["status"] == "SUCCESS"
    assert summary["analyses_normalized_count"] == 1
    assert summary["recommendations_generated_count"] == 1
    assert "trusted/soil_analyses_normalized/dt=2026-10-15" in summary["s3_parquet_uri"]
    assert summary["postgres_upserted_analyses"] == 1
    assert summary["postgres_upserted_recommendations"] == 1
    mock_s3.put_object.assert_called_once()
    mock_glue.create_partition.assert_called_once()
