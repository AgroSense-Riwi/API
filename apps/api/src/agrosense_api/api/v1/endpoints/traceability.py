from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_current_buyer_id
from agrosense_api.db.session import get_db
from agrosense_api.schemas.traceability import BatchSummaryResponse, TraceabilityResponse
from agrosense_api.services.traceability import (
    TraceabilityForbiddenError,
    TraceabilityNotFoundError,
    get_batch_trace,
    get_buyer_batches,
)

router = APIRouter(prefix="/traceability", tags=["Compradores"])


@router.get(
    "/batches",
    response_model=list[BatchSummaryResponse],
    summary="Listar lotes del comprador",
    description=(
        "Consulta todos los lotes de café asignados contractualmente al comprador autenticado."
    ),
)
async def list_buyer_batches(
    session: Annotated[AsyncSession, Depends(get_db)],
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
) -> list[BatchSummaryResponse]:
    return await get_buyer_batches(session, buyer_id)


@router.get(
    "/{batch_code}",
    response_model=TraceabilityResponse,
    summary="Consultar trazabilidad de un lote",
    description=(
        "Consulta comercial de certificado y trazabilidad de un lote de exportación propio."
    ),
)
async def read_traceability(
    batch_code: Annotated[
        str,
        Path(min_length=1, max_length=100, description="Código único del lote"),
    ],
    session: Annotated[AsyncSession, Depends(get_db)],
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
) -> TraceabilityResponse:
    try:
        return await get_batch_trace(session, batch_code, buyer_id)
    except TraceabilityForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except TraceabilityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
