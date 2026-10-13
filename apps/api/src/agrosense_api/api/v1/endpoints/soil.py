from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.db.session import get_db
from agrosense_api.schemas.soil import SoilRecommendationResponse
from agrosense_api.services.agronomy import get_soil_recommendations

router = APIRouter(
    prefix="/soil",
    tags=["soil"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/recommendations/{farm_id}",
    response_model=SoilRecommendationResponse,
)
async def read_soil_recommendations(
    farm_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SoilRecommendationResponse:
    recommendations = await get_soil_recommendations(session, farm_id)
    if recommendations is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No soil analysis found for this farm",
        )
    return recommendations
