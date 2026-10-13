from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.db.session import get_db
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.schemas.errors import ErrorResponse
from agrosense_api.schemas.lots import (
    LotCertificateResponse,
    LotCertifyRequest,
    LotResponse,
)
from agrosense_api.services.lots import LotCertificationError, certify_lot, get_lot

router = APIRouter(prefix="/lots", tags=["lots"])


@router.get(
    "/{lot_id}",
    response_model=LotResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Missing lots:read scope"},
        404: {"model": ErrorResponse, "description": "Lot not found"},
        422: {"model": ErrorResponse, "description": "Invalid lot identifier"},
    },
)
async def read_lot(
    lot_id: Annotated[UUID, Path(description="UUID of the export lot")],
    session: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[AuthenticatedUser, Security(get_current_user, scopes=["lots:read"])],
) -> LotResponse:
    lot = await get_lot(session, lot_id)
    if lot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    return lot


@router.post(
    "/certify",
    response_model=LotCertificateResponse,
    status_code=status.HTTP_200_OK,
    responses={
        400: {"model": ErrorResponse, "description": "Certification precondition failed"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Missing lots:certify scope"},
        404: {"model": ErrorResponse, "description": "Lot not found"},
        409: {"model": ErrorResponse, "description": "Lot cannot transition from current state"},
        422: {"model": ErrorResponse, "description": "Invalid request body"},
    },
)
async def certify_export_lot(
    payload: LotCertifyRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[AuthenticatedUser, Security(get_current_user, scopes=["lots:certify"])],
) -> LotCertificateResponse:
    try:
        return await certify_lot(session, payload.lot_id)
    except LotCertificationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
