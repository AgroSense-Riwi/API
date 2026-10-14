"""Tests for F6 soil-report normalization and the F1 reference integration."""

from io import BytesIO

import pytest

from scripts.seed.faker_generators import AgroSenseFakerGenerator
from scripts.seed.soil import (
    compare_soil_analyses_to_f1,
    load_f1_crop_reference,
    normalize_soil_analysis,
    summarize_f1_csv,
)


def test_normalize_soil_report_converts_mixed_units_and_preserves_source_data():
    report = {
        "analysis_id": "analysis-1",
        "farm_id": "farm-1",
        "plot_id": "plot-1",
        "sample_date": "2026-10-14",
        "ph_level": 5.4,
        "organic_matter_pct": 4.2,
        "soil_texture": "LOAM",
        "quality_defects": ["DEFECT_MIXED_UNITS"],
        "nutrient_measurements": {
            "nitrogen": {"value": 0.05, "unit": "g/kg"},
            "phosphorus": {"value": 25, "unit": "ppm"},
            "potassium": {"value": 0.5, "unit": "cmol/kg"},
        },
    }

    normalized = normalize_soil_analysis(report)

    assert normalized["nitrogen_mg_kg"] == 50
    assert normalized["phosphorus_mg_kg"] == 25
    assert normalized["potassium_mg_kg"] == 195.49
    assert normalized["source_data"]["nutrient_measurements"] == report["nutrient_measurements"]
    assert normalized["source_data"]["quality_defects"] == ["DEFECT_MIXED_UNITS"]
    assert normalized["measured_at"] == "2026-10-14T00:00:00Z"


def test_normalize_soil_report_rejects_unknown_and_invalid_units():
    report = {
        "analysis_id": "analysis-1",
        "farm_id": "farm-1",
        "sample_date": "2026-10-14",
        "ph_level": 5.4,
        "nutrient_measurements": {
            "nitrogen": {"value": 20, "unit": "kg/ha"},
            "phosphorus": {"value": 25, "unit": "mg/kg"},
            "potassium": {"value": 200, "unit": "mg/kg"},
        },
    }
    with pytest.raises(ValueError, match="Unidad de suelo no soportada"):
        normalize_soil_analysis(report)

    report["nutrient_measurements"]["nitrogen"] = {"value": float("nan"), "unit": "mg/kg"}
    with pytest.raises(ValueError, match="número finito"):
        normalize_soil_analysis(report)


def test_generator_injects_reproducible_mixed_unit_defects_and_normalizes():
    farms = [
        {"farm_id": "farm-1", "farm_code": "FIN-0001"},
        {"farm_id": "farm-2", "farm_code": "FIN-0002"},
    ]
    generator_a = AgroSenseFakerGenerator(seed=42)
    reports_a = generator_a.generate_soil_lab_analyses(farms, count=10, defect_rate=0.3)
    generator_b = AgroSenseFakerGenerator(seed=42)
    reports_b = generator_b.generate_soil_lab_analyses(farms, count=10, defect_rate=0.3)

    assert reports_a == reports_b
    defective_reports = [
        report for report in reports_a if "DEFECT_MIXED_UNITS" in report["quality_defects"]
    ]
    assert len(defective_reports) == 3
    assert all(
        len({measurement["unit"] for measurement in report["nutrient_measurements"].values()}) > 1
        for report in defective_reports
    )
    assert len(
        AgroSenseFakerGenerator(seed=42).generate_soil_lab_analyses(
            farms, count=10, defect_rate=0
        )
    ) == 10

    normalized = [normalize_soil_analysis(report) for report in reports_a]
    assert len(normalized) == 10
    assert all(
        {"id", "farm_id", "ph", "nitrogen_mg_kg", "phosphorus_mg_kg", "potassium_mg_kg"}
        <= analysis.keys()
        for analysis in normalized
    )


def test_f1_csv_summary_and_unit_safe_comparison():
    csv_content = (
        "N,P,K,temperature,humidity,ph,rainfall,label\n"
        "40,30,20,25,80,6,100,coffee\n"
        "60,50,40,24,75,5.5,120,coffee\n"
        "90,70,50,22,70,6,90,maize\n"
    )
    reference = summarize_f1_csv(csv_content)
    assert reference["nutrients"]["nitrogen"] == {
        "min": 40.0,
        "median": 50.0,
        "max": 60.0,
        "sample_count": 2,
    }
    assert reference["nutrients"]["ph"] == {
        "min": 5.5,
        "median": 5.75,
        "max": 6.0,
        "sample_count": 2,
    }

    analysis = {
        "id": "analysis-1",
        "ph": 5.6,
        "nitrogen_mg_kg": 50,
        "phosphorus_mg_kg": 15,
        "potassium_mg_kg": 60,
    }
    comparisons = compare_soil_analyses_to_f1([analysis], reference, f1_npk_unit="ppm")
    assert comparisons[0]["nutrient_range_status"] == {
        "nitrogen": "within_f1_range",
        "phosphorus": "below_f1_range",
        "potassium": "above_f1_range",
    }
    assert comparisons[0]["ph_range_status"] == "within_f1_range"
    with pytest.raises(ValueError, match="La unidad N/P/K"):
        compare_soil_analyses_to_f1([analysis], reference, f1_npk_unit="kg/ha")


def test_f1_csv_requires_crop_rows_and_required_columns():
    with pytest.raises(ValueError, match="F1 no contiene registros"):
        summarize_f1_csv("N,P,K,ph,label\n1,2,3,6,maize\n")
    with pytest.raises(ValueError, match="Faltan columnas"):
        summarize_f1_csv("n,p,label\n1,2,coffee\n")


def test_f1_loader_reads_the_requested_s3_object(monkeypatch):
    csv_content = b"N,P,K,ph,label\n40,30,20,5.5,coffee\n"
    received: dict[str, str] = {}

    class FakeS3Client:
        def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
            received.update({"bucket": Bucket, "key": Key})
            return {"Body": BytesIO(csv_content)}

    monkeypatch.setattr("scripts.seed.soil.boto3.client", lambda service: FakeS3Client())
    result = load_f1_crop_reference(
        "s3://data-lake-agrosense/bronze/kaggle_crop_reference/year=2026/"
        "month=10/day=14/Crop_recommendation.csv"
    )

    assert received == {
        "bucket": "data-lake-agrosense",
        "key": (
            "bronze/kaggle_crop_reference/year=2026/month=10/day=14/"
            "Crop_recommendation.csv"
        ),
    }
    assert result["crop_label"] == "coffee"
