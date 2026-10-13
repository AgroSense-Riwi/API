from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.models import SoilAnalysis
from agrosense_api.schemas.soil import SoilRecommendationResponse


async def get_soil_recommendations(
    session: AsyncSession, farm_id: UUID
) -> SoilRecommendationResponse | None:
    query = (
        select(SoilAnalysis)
        .where(SoilAnalysis.farm_id == farm_id)
        .order_by(SoilAnalysis.measured_at.desc())
        .limit(1)
    )
    analysis = await session.scalar(query)
    if analysis is None:
        return None

    recommendations: list[str] = []
    if analysis.ph < 5.0:
        recommendations.append("Soil is strongly acidic; request an agronomist's lime assessment.")
    elif analysis.ph > 6.5:
        recommendations.append(
            "Soil pH is above the usual coffee target; confirm locally before treatment."
        )
    else:
        recommendations.append("Measured pH is within the broad coffee-growing target range.")

    for nutrient, value in (
        ("nitrogen", analysis.nitrogen_mg_kg),
        ("phosphorus", analysis.phosphorus_mg_kg),
        ("potassium", analysis.potassium_mg_kg),
    ):
        if value < 10:
            recommendations.append(
                f"Measured {nutrient} is low; confirm with a calibrated lab and agronomist."
            )

    return SoilRecommendationResponse(
        farm_id=analysis.farm_id,
        measured_at=analysis.measured_at,
        ph=analysis.ph,
        recommendations=recommendations,
    )
