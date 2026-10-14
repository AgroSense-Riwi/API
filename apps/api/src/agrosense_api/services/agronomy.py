from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.core.config import get_settings
from agrosense_api.models import (
    CropNutrientReference,
    FarmPlot,
    FertilizerRecommendation,
    SoilAnalysis,
)
from agrosense_api.schemas.soil import SoilRecommendationResponse

DOSAGE_QUANTUM = Decimal("0.01")
NUTRIENT_FIELDS = {
    "n": ("nitrogen_mg_kg", "n_min_mg_kg", "n_max_mg_kg", "req_nitrogen_kg_ha"),
    "p": ("phosphorus_mg_kg", "p_min_mg_kg", "p_max_mg_kg", "req_phosphorus_kg_ha"),
    "k": ("potassium_mg_kg", "k_min_mg_kg", "k_max_mg_kg", "req_potassium_kg_ha"),
}
NUTRIENT_WARNING_DETAILS = {
    "n": ("nitrógeno", "nitrogenado"),
    "p": ("fósforo", "fosfatado"),
    "k": ("potasio", "potásico"),
}


class SoilReferenceNotFoundError(ValueError):
    """Raised when an analysis' plot has no matching variety and growth-stage reference."""


def evaluate_fertilizer_recommendation(
    analysis: Any,
    reference: Any,
    *,
    traditional_baseline_kg_ha: Decimal | float = Decimal("200"),
    fertilizer_dose_multiplier: Decimal | float = Decimal("1"),
) -> dict[str, Any]:
    """Apply RN-07, using reference-table kg/ha requirements for deficient nutrients."""
    baseline = _decimal(traditional_baseline_kg_ha, "traditional_baseline_kg_ha")
    if baseline <= 0:
        raise ValueError("traditional_baseline_kg_ha debe ser mayor que cero.")
    dose_multiplier = _decimal(fertilizer_dose_multiplier, "fertilizer_dose_multiplier")
    if dose_multiplier < 0:
        raise ValueError("fertilizer_dose_multiplier no puede ser negativo.")

    result: dict[str, Any] = {
        "n_evaluation": "",
        "p_evaluation": "",
        "k_evaluation": "",
        "ph_evaluation": "",
        "recommended_n_kg_ha": Decimal("0.00"),
        "recommended_p_kg_ha": Decimal("0.00"),
        "recommended_k_kg_ha": Decimal("0.00"),
        "apply_fertilizer": True,
        "estimated_saving_pct": Decimal("0.00"),
        "recommendations": [],
    }
    any_nutrient_excess = False

    for nutrient, (
        analysis_field,
        minimum_field,
        maximum_field,
        req_field,
    ) in NUTRIENT_FIELDS.items():
        actual = _decimal(getattr(analysis, analysis_field), analysis_field)
        minimum = _decimal(getattr(reference, minimum_field), minimum_field)
        maximum = _decimal(getattr(reference, maximum_field), maximum_field)
        if minimum > maximum:
            raise ValueError(f"Rango de referencia inválido para {nutrient.upper()}.")

        evaluation_field = f"{nutrient}_evaluation"
        dose_field = f"recommended_{nutrient}_kg_ha"
        if actual < minimum:
            reference_dose = _decimal(getattr(reference, req_field), req_field)
            if reference_dose < 0:
                raise ValueError(f"{req_field} no puede ser negativo.")
            result[evaluation_field] = "DEFICIENT"
            dose = max(Decimal("0"), reference_dose * dose_multiplier)
            result[dose_field] = dose.quantize(DOSAGE_QUANTUM, rounding=ROUND_HALF_UP)
            result["recommendations"].append(
                f"{nutrient.upper()} está por debajo del rango de referencia; "
                f"dosis de compensación de {result[dose_field]} kg/ha según F1."
            )
        elif actual > maximum:
            result[evaluation_field] = "EXCESS_WARNING"
            any_nutrient_excess = True
            nutrient_name, fertilizer_name = NUTRIENT_WARNING_DETAILS[nutrient]
            result["recommendations"].append(
                f"Se detectó exceso de {nutrient_name} en suelo. No aplicar fertilizante "
                f"{fertilizer_name} para evitar fitotoxicidad y acidificación."
            )
        else:
            result[evaluation_field] = "OPTIMAL"

    ph = _decimal(analysis.ph, "ph")
    ph_minimum = _decimal(reference.target_ph_min, "target_ph_min")
    ph_maximum = _decimal(reference.target_ph_max, "target_ph_max")
    if ph_minimum > ph_maximum:
        raise ValueError("El rango de pH de referencia es inválido.")
    if ph < ph_minimum:
        result["ph_evaluation"] = "ACIDIC"
        result["recommendations"].append(
            "pH por debajo del rango de referencia; evaluar corrección de acidez."
        )
    elif ph > ph_maximum:
        result["ph_evaluation"] = "ALKALINE"
        result["recommendations"].append(
            "pH por encima del rango de referencia; revisar alcalinidad antes de aplicar insumos."
        )
    else:
        result["ph_evaluation"] = "OPTIMAL"

    result["apply_fertilizer"] = not any_nutrient_excess
    total_dose = sum((result[f"recommended_{nutrient}_kg_ha"] for nutrient in "npk"), Decimal("0"))
    saving = max(Decimal("0"), ((baseline - total_dose) / baseline) * Decimal("100"))
    result["estimated_saving_pct"] = min(
        Decimal("100"), saving.quantize(DOSAGE_QUANTUM, rounding=ROUND_HALF_UP)
    )
    return result


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ArithmeticError, ValueError) as err:
        raise ValueError(f"{field} debe ser numérico.") from err
    if not result.is_finite():
        raise ValueError(f"{field} debe ser finito.")
    return result


async def get_soil_recommendations(
    session: AsyncSession, farm_id: UUID
) -> SoilRecommendationResponse | None:
    async with session.begin():
        return await _get_soil_recommendations_in_transaction(session, farm_id=farm_id)


async def get_plot_soil_recommendations(
    session: AsyncSession, plot_id: UUID
) -> SoilRecommendationResponse | None:
    async with session.begin():
        return await _get_soil_recommendations_in_transaction(session, plot_id=plot_id)


async def _get_soil_recommendations_in_transaction(
    session: AsyncSession,
    *,
    farm_id: UUID | None = None,
    plot_id: UUID | None = None,
) -> SoilRecommendationResponse | None:
    if (farm_id is None) == (plot_id is None):
        raise ValueError("Debe especificarse exactamente una finca o un lote.")
    query = (
        select(SoilAnalysis, FarmPlot, CropNutrientReference)
        .outerjoin(FarmPlot, SoilAnalysis.plot_id == FarmPlot.plot_id)
        .outerjoin(
            CropNutrientReference,
            and_(
                FarmPlot.variety_id == CropNutrientReference.variety_id,
                FarmPlot.growth_stage == CropNutrientReference.growth_stage,
            ),
        )
        .order_by(SoilAnalysis.measured_at.desc())
        .limit(1)
    )
    if plot_id is not None:
        query = query.where(SoilAnalysis.plot_id == plot_id)
    else:
        query = query.where(SoilAnalysis.farm_id == farm_id)
    row = (await session.execute(query)).one_or_none()
    if row is None:
        return None

    analysis, plot, reference = row
    if plot is None or reference is None:
        raise SoilReferenceNotFoundError(
            "No hay lote o referencia F1 para la variedad y etapa del análisis de suelo."
        )

    settings = get_settings()
    result = evaluate_fertilizer_recommendation(
        analysis,
        reference,
        traditional_baseline_kg_ha=Decimal(str(settings.fertilizer_baseline_kg_ha)),
        fertilizer_dose_multiplier=Decimal(str(settings.fertilizer_dose_multiplier)),
    )
    recommendation = await session.scalar(
        select(FertilizerRecommendation).where(FertilizerRecommendation.analysis_id == analysis.id)
    )
    if recommendation is None:
        recommendation = FertilizerRecommendation(
            analysis_id=analysis.id,
            plot_id=plot.plot_id,
            farm_id=analysis.farm_id,
            generated_at=datetime.now(UTC),
            **{key: value for key, value in result.items() if key != "recommendations"},
        )
        session.add(recommendation)
    else:
        recommendation.plot_id = plot.plot_id
        recommendation.farm_id = analysis.farm_id
        recommendation.generated_at = datetime.now(UTC)
        for key, value in result.items():
            if key != "recommendations":
                setattr(recommendation, key, value)
    await session.flush()

    return SoilRecommendationResponse(
        analysis_id=analysis.id,
        farm_id=analysis.farm_id,
        plot_id=plot.plot_id,
        measured_at=analysis.measured_at,
        ph=analysis.ph,
        n_evaluation=result["n_evaluation"],
        p_evaluation=result["p_evaluation"],
        k_evaluation=result["k_evaluation"],
        ph_evaluation=result["ph_evaluation"],
        apply_fertilizer=result["apply_fertilizer"],
        recommended_n_kg_ha=result["recommended_n_kg_ha"],
        recommended_p_kg_ha=result["recommended_p_kg_ha"],
        recommended_k_kg_ha=result["recommended_k_kg_ha"],
        estimated_saving_pct=result["estimated_saving_pct"],
        recommendations=result["recommendations"],
    )
