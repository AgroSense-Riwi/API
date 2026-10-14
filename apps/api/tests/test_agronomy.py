from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from agrosense_api.db.base import Base
from agrosense_api.models import FarmPlot, FertilizerRecommendation, SoilAnalysis
from agrosense_api.services.agronomy import evaluate_fertilizer_recommendation
from sqlalchemy.orm import configure_mappers

from scripts.seed.soil import normalize_soil_analysis

FARM_ID = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
PLOT_ID = UUID("abd9d707-5771-5d7a-a64f-f63c6a0d33ad")
ANALYSIS_ID = UUID("38658068-bc9b-53ed-91f7-0178fdaf3ed3")


def _reference() -> SimpleNamespace:
    return SimpleNamespace(
        n_min_mg_kg=50,
        n_max_mg_kg=100,
        p_min_mg_kg=20,
        p_max_mg_kg=50,
        k_min_mg_kg=40,
        k_max_mg_kg=100,
        target_ph_min=5,
        target_ph_max=5.8,
        req_nitrogen_kg_ha=150,
        req_phosphorus_kg_ha=30,
        req_potassium_kg_ha=120,
    )


def _analysis(
    *,
    nitrogen_mg_kg: float = 75,
    phosphorus_mg_kg: float = 35,
    potassium_mg_kg: float = 65,
    ph: float = 5.5,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=ANALYSIS_ID,
        farm_id=FARM_ID,
        plot_id=PLOT_ID,
        measured_at=datetime(2026, 10, 14, tzinfo=UTC),
        nitrogen_mg_kg=nitrogen_mg_kg,
        phosphorus_mg_kg=phosphorus_mg_kg,
        potassium_mg_kg=potassium_mg_kg,
        ph=ph,
    )


def test_fertilizer_recommendation_deficient():
    result = evaluate_fertilizer_recommendation(
        _analysis(nitrogen_mg_kg=25),
        _reference(),
        traditional_baseline_kg_ha=200,
    )

    assert result["n_evaluation"] == "DEFICIENT"
    assert result["recommended_n_kg_ha"] == 150
    assert result["recommended_p_kg_ha"] == 0
    assert result["recommended_k_kg_ha"] == 0
    assert result["estimated_saving_pct"] > 0
    assert result["estimated_saving_pct"] == 25
    assert result["apply_fertilizer"] is True


def test_recommendation_dose_multiplier_is_configurable():
    result = evaluate_fertilizer_recommendation(
        _analysis(nitrogen_mg_kg=25),
        _reference(),
        fertilizer_dose_multiplier=0.5,
    )

    assert result["recommended_n_kg_ha"] == 75
    assert result["estimated_saving_pct"] == 62.5


def test_fertilizer_recommendation_excess_warning():
    result = evaluate_fertilizer_recommendation(
        _analysis(nitrogen_mg_kg=101),
        _reference(),
    )

    assert result["n_evaluation"] == "EXCESS_WARNING"
    assert result["recommended_n_kg_ha"] == 0
    assert result["apply_fertilizer"] is False
    assert any(
        "Se detectó exceso de nitrógeno en suelo. No aplicar fertilizante nitrogenado"
        in recommendation
        for recommendation in result["recommendations"]
    )


def test_excess_warnings_for_each_nutrient_and_alkaline_ph_do_not_block():
    result = evaluate_fertilizer_recommendation(
        _analysis(nitrogen_mg_kg=101, phosphorus_mg_kg=51, potassium_mg_kg=101, ph=5.9),
        _reference(),
    )

    assert result["n_evaluation"] == "EXCESS_WARNING"
    assert result["p_evaluation"] == "EXCESS_WARNING"
    assert result["k_evaluation"] == "EXCESS_WARNING"
    assert result["ph_evaluation"] == "ALKALINE"
    assert result["apply_fertilizer"] is False
    assert result["recommended_n_kg_ha"] == 0
    assert result["recommended_p_kg_ha"] == 0
    assert result["recommended_k_kg_ha"] == 0
    assert len(result["recommendations"]) == 4
    assert any("fertilizante nitrogenado" in item for item in result["recommendations"])
    assert any("fertilizante fosfatado" in item for item in result["recommendations"])
    assert any("fertilizante potásico" in item for item in result["recommendations"])


def test_identical_recommendation_across_normalized_units():
    base_report = {
        "analysis_id": str(ANALYSIS_ID),
        "farm_id": str(FARM_ID),
        "plot_id": str(PLOT_ID),
        "sample_date": "2026-10-14",
        "ph_level": 5.5,
        "nutrient_measurements": {
            "nitrogen": {"value": 25, "unit": "mg/kg"},
            "phosphorus": {"value": 35, "unit": "mg/kg"},
            "potassium": {"value": 65, "unit": "mg/kg"},
        },
    }
    ppm_report = {
        **base_report,
        "nutrient_measurements": {
            nutrient: {"value": measurement["value"], "unit": "ppm"}
            for nutrient, measurement in base_report["nutrient_measurements"].items()
        },
    }

    mg_kg_analysis = normalize_soil_analysis(base_report)
    ppm_analysis = normalize_soil_analysis(ppm_report)
    mg_kg_result = evaluate_fertilizer_recommendation(
        SimpleNamespace(**mg_kg_analysis), _reference()
    )
    ppm_result = evaluate_fertilizer_recommendation(SimpleNamespace(**ppm_analysis), _reference())

    assert mg_kg_analysis["nitrogen_mg_kg"] == ppm_analysis["nitrogen_mg_kg"] == 25
    assert mg_kg_result == ppm_result


def test_ph_values_classify_as_acidic_optimal_or_alkaline():
    assert (
        evaluate_fertilizer_recommendation(_analysis(ph=4.9), _reference())["ph_evaluation"]
        == "ACIDIC"
    )
    assert (
        evaluate_fertilizer_recommendation(_analysis(ph=5.8), _reference())["ph_evaluation"]
        == "OPTIMAL"
    )
    assert (
        evaluate_fertilizer_recommendation(_analysis(ph=5.9), _reference())["ph_evaluation"]
        == "ALKALINE"
    )
    alkaline_only = evaluate_fertilizer_recommendation(_analysis(ph=5.9), _reference())
    assert alkaline_only["apply_fertilizer"] is True


def test_optimal_nutrient_boundaries_produce_no_dose():
    result = evaluate_fertilizer_recommendation(
        _analysis(nitrogen_mg_kg=50, phosphorus_mg_kg=50, potassium_mg_kg=40),
        _reference(),
    )

    assert result["n_evaluation"] == "OPTIMAL"
    assert result["p_evaluation"] == "OPTIMAL"
    assert result["k_evaluation"] == "OPTIMAL"
    assert result["recommended_n_kg_ha"] == 0
    assert result["recommended_p_kg_ha"] == 0
    assert result["recommended_k_kg_ha"] == 0


def test_recommendation_models_are_registered_and_mappers_configure():
    configure_mappers()

    assert "farm_plots" in Base.metadata.tables
    assert "fertilizer_recommendations" in Base.metadata.tables
    assert "soil_analyses" in Base.metadata.tables
    assert FertilizerRecommendation.__table__.c.analysis_id.unique
    assert FarmPlot.__table__.c.variety_id.foreign_keys
    assert SoilAnalysis.__table__.c.plot_id.foreign_keys
