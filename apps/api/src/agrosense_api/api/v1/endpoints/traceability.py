from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_buyer_id
from agrosense_api.db.session import get_db
from agrosense_api.schemas.errors import ErrorResponse
from agrosense_api.schemas.traceability import TraceabilityResponse
from agrosense_api.services.traceability import TraceabilityForbiddenError, get_batch_trace

router = APIRouter(prefix="/traceability", tags=["Compradores"])


@router.get(
    "/{batch_code}",
    response_model=TraceabilityResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or missing buyer token"},
        403: {"model": ErrorResponse, "description": "Lot belongs to another buyer"},
        404: {"model": ErrorResponse, "description": "Export batch not found"},
    },
)
async def read_traceability(
    batch_code: Annotated[str, Path(min_length=1, max_length=100)],
    session: Annotated[AsyncSession, Depends(get_db)],
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
) -> TraceabilityResponse:
    try:
        trace = await get_batch_trace(session, batch_code, buyer_id)
    except TraceabilityForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export batch not found",
        )
    return trace
