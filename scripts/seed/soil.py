"""Soil-report normalization and F1 crop-reference utilities."""

from __future__ import annotations

import csv
import io
import math
import statistics
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

F1_REQUIRED_COLUMNS = {"n", "p", "k", "ph", "label"}
DEFAULT_F1_NPK_UNIT = "mg/kg"
NUTRIENTS = ("nitrogen", "phosphorus", "potassium")
F1_NUTRIENT_COLUMNS = {
    "nitrogen": "n",
    "phosphorus": "p",
    "potassium": "k",
}
F1_FEATURE_COLUMNS = {**F1_NUTRIENT_COLUMNS, "ph": "ph"}
SOIL_UNIT_TO_MG_KG = {
    "mg/kg": 1.0,
    "ppm": 1.0,
    "g/kg": 1000.0,
    "%": 10_000.0,
}
POTASSIUM_CMOL_TO_MG_KG = 390.98


def normalize_soil_analysis(report: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw laboratory report into the API's canonical soil-analysis shape."""
    required_fields = {
        "analysis_id",
        "farm_id",
        "sample_date",
        "ph_level",
        "nutrient_measurements",
    }
    missing = required_fields - report.keys()
    if missing:
        raise ValueError(
            f"Falta(n) campo(s) requerido(s) en el reporte de suelo: {sorted(missing)}"
        )

    measurements = report["nutrient_measurements"]
    if not isinstance(measurements, dict):
        raise ValueError("nutrient_measurements debe ser un diccionario.")

    normalized: dict[str, Any] = {
        "id": report["analysis_id"],
        "farm_id": report["farm_id"],
        "plot_id": report.get("plot_id"),
        "ph": _finite_number(report["ph_level"], "ph_level"),
        "measured_at": f"{report['sample_date']}T00:00:00Z",
    }
    for nutrient in NUTRIENTS:
        measurement = measurements.get(nutrient)
        if (
            not isinstance(measurement, dict)
            or "value" not in measurement
            or "unit" not in measurement
        ):
            raise ValueError(f"Medición inválida para el nutriente {nutrient}.")
        value = _finite_number(measurement["value"], f"{nutrient}.value")
        unit = measurement["unit"]
        if not isinstance(unit, str):
            raise ValueError(f"La unidad de {nutrient} debe ser texto.")
        normalized[f"{nutrient}_mg_kg"] = _convert_to_mg_kg(nutrient, value, unit)

    normalized["source_data"] = {
        "nutrient_measurements": measurements,
        "quality_defects": report.get("quality_defects", []),
        "plot_id": report.get("plot_id"),
        "organic_matter_pct": report.get("organic_matter_pct"),
        "soil_texture": report.get("soil_texture"),
        "source": "F6",
    }
    return normalized


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field} debe ser un número finito.")
    return float(value)


def _convert_to_mg_kg(nutrient: str, value: float, unit: str) -> float:
    normalized_unit = unit.strip().casefold().replace(" ", "")
    if normalized_unit == "cmol/kg":
        if nutrient != "potassium":
            raise ValueError(f"cmol/kg no está soportada para {nutrient}.")
        return round(value * POTASSIUM_CMOL_TO_MG_KG, 2)

    factor = SOIL_UNIT_TO_MG_KG.get(normalized_unit)
    if factor is None:
        raise ValueError(f"Unidad de suelo no soportada para {nutrient}: {unit!r}.")
    return round(value * factor, 2)


def load_f1_crop_reference(s3_uri: str) -> dict[str, Any]:
    """Load the F1 Kaggle CSV from S3 and summarize the crop-recommendation rows."""
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError("La referencia F1 debe usar el formato s3://bucket/ruta/archivo.csv.")

    try:
        response = boto3.client("s3").get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
        body = response["Body"].read()
    except (BotoCoreError, ClientError, KeyError) as err:
        raise RuntimeError(f"No se pudo leer la referencia F1 desde {s3_uri}.") from err

    return summarize_f1_csv(body.decode("utf-8-sig"))


def summarize_f1_csv(csv_content: str, crop_label: str = "coffee") -> dict[str, Any]:
    """Summarize N/P/K values for one crop label in the Kaggle recommendation CSV."""
    reader = csv.DictReader(io.StringIO(csv_content))
    if reader.fieldnames is None:
        raise ValueError("El CSV de F1 está vacío o no tiene encabezados.")
    column_names = {name.strip().casefold(): name for name in reader.fieldnames if name}
    missing = F1_REQUIRED_COLUMNS - column_names.keys()
    if missing:
        raise ValueError(f"Faltan columnas requeridas en F1: {sorted(missing)}")

    values: dict[str, list[float]] = {feature: [] for feature in F1_FEATURE_COLUMNS}
    target_label = crop_label.casefold()
    for row in reader:
        if (row.get(column_names["label"]) or "").strip().casefold() != target_label:
            continue
        for feature, column in F1_FEATURE_COLUMNS.items():
            try:
                value = float(row[column_names[column]])
            except (TypeError, ValueError) as err:
                raise ValueError(f"Valor no numérico en la columna F1 {column!r}.") from err
            if not math.isfinite(value):
                raise ValueError(f"Valor no finito en la columna F1 {column!r}.")
            values[feature].append(value)

    if not all(values.values()):
        raise ValueError(f"F1 no contiene registros completos para el cultivo {crop_label!r}.")

    profile = {
        nutrient: {
            "min": min(samples),
            "median": statistics.median(samples),
            "max": max(samples),
            "sample_count": len(samples),
        }
        for nutrient, samples in values.items()
    }
    return {"crop_label": crop_label, "nutrients": profile}


def compare_soil_analyses_to_f1(
    analyses: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    f1_npk_unit: str,
) -> list[dict[str, Any]]:
    """Compare normalized soil values with F1 ranges when their units are compatible."""
    unit = f1_npk_unit.strip().casefold().replace(" ", "")
    if unit not in {"mg/kg", "ppm"}:
        raise ValueError(
            "La unidad N/P/K de F1 debe confirmarse como mg/kg o ppm para comparar "
            "con el suelo; no se asume una conversión desde kg/ha."
        )

    comparisons: list[dict[str, Any]] = []
    for analysis in analyses:
        nutrient_results: dict[str, str] = {}
        for nutrient in NUTRIENTS:
            value = _finite_number(analysis[f"{nutrient}_mg_kg"], f"{nutrient}_mg_kg")
            bounds = reference["nutrients"][nutrient]
            if value < bounds["min"]:
                status = "below_f1_range"
            elif value > bounds["max"]:
                status = "above_f1_range"
            else:
                status = "within_f1_range"
            nutrient_results[nutrient] = status
        ph = _finite_number(analysis["ph"], "ph")
        ph_bounds = reference["nutrients"]["ph"]
        if ph < ph_bounds["min"]:
            ph_status = "below_f1_range"
        elif ph > ph_bounds["max"]:
            ph_status = "above_f1_range"
        else:
            ph_status = "within_f1_range"
        comparisons.append(
            {
                "soil_analysis_id": analysis["id"],
                "crop_label": reference["crop_label"],
                "f1_npk_unit": unit,
                "nutrient_range_status": nutrient_results,
                "ph_range_status": ph_status,
                "interpretation": (
                    "Comparación descriptiva con el rango de muestras F1; "
                    "no constituye una dosis ni una prescripción de fertilizante."
                ),
            }
        )
    return comparisons
