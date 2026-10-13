from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.db.session import get_db
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.schemas.errors import ErrorResponse
from agrosense_api.schemas.farms import FarmResponse
from agrosense_api.services.lots import get_farm

router = APIRouter(prefix="/farms", tags=["farms"])


@router.get(
    "/{farm_id}",
    response_model=FarmResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Missing farms:read scope"},
        404: {"model": ErrorResponse, "description": "Farm not found"},
        422: {"model": ErrorResponse, "description": "Invalid farm identifier"},
    },
)
async def read_farm(
    farm_id: Annotated[UUID, Path(description="UUID of the registered farm")],
    session: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[AuthenticatedUser, Security(get_current_user, scopes=["farms:read"])],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FarmResponse:
    farm = await get_farm(session, farm_id, limit=limit, offset=offset)
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farm not found")
    return farm
