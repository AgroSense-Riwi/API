import time
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_buyer_db, get_current_buyer_id
from agrosense_api.schemas.traceability import BatchSummaryResponse, TraceabilityResponse
from agrosense_api.services.buyer_audit import record_buyer_api_access
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
    session: Annotated[AsyncSession, Depends(get_buyer_db)],
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
) -> list[BatchSummaryResponse]:
    # Doble barrera: RLS en PostgreSQL (app.current_buyer_id) + filtro explícito por buyer_id.
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
    session: Annotated[AsyncSession, Depends(get_buyer_db)],
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
) -> TraceabilityResponse:
    # CA-09 / RNF-05: cada 200 y 403 queda en buyer_api_audit_logs con su latencia.
    requested_at = datetime.now(UTC)
    started = time.perf_counter()

    async def audit(http_status_code: int) -> None:
        await record_buyer_api_access(
            session,
            batch_code=batch_code,
            http_status_code=http_status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
            requested_at=requested_at,
        )

    try:
        trace = await get_batch_trace(session, batch_code, buyer_id)
    except TraceabilityForbiddenError as exc:
        await audit(status.HTTP_403_FORBIDDEN)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except TraceabilityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    await audit(status.HTTP_200_OK)
    return trace
