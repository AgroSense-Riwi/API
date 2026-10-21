import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agrosense_api.models.batch import BatchFarmComposition, ExportBatch
from agrosense_api.models.buyer import BuyerBatchContract
from agrosense_api.schemas.traceability import (
    BatchCompositionItem,
    BatchSummaryResponse,
    CuppingEvaluationItem,
    TraceabilityResponse,
)


class TraceabilityForbiddenError(Exception):
    pass


class TraceabilityNotFoundError(Exception):
    pass


async def get_buyer_batches(
    session: AsyncSession, buyer_id: uuid.UUID
) -> list[BatchSummaryResponse]:
    """Obtiene el listado de lotes asignados contractualmente a un comprador."""
    stmt = (
        select(ExportBatch)
        .join(BuyerBatchContract, BuyerBatchContract.batch_id == ExportBatch.batch_id)
        .where(BuyerBatchContract.buyer_id == buyer_id)
        .order_by(ExportBatch.created_at.desc())
    )
    result = await session.scalars(stmt)
    batches = result.all()
    return [BatchSummaryResponse.model_validate(b) for b in batches]


async def get_batch_trace(
    session: AsyncSession, batch_code: str, buyer_id: uuid.UUID
) -> TraceabilityResponse:
    """Obtiene el detalle de trazabilidad de un lote si pertenece al comprador."""
    stmt = (
        select(ExportBatch)
        .options(
            selectinload(ExportBatch.compositions).selectinload(BatchFarmComposition.farm),
            selectinload(ExportBatch.cupping_evaluations),
            selectinload(ExportBatch.contracts).selectinload(BuyerBatchContract.buyer),
        )
        .where(ExportBatch.batch_code == batch_code)
    )
    batch = await session.scalar(stmt)
    if batch is None:
        raise TraceabilityNotFoundError("Lote de exportación no encontrado")

    # Validar que el comprador tenga contrato con este lote
    buyer_contract = next((c for c in batch.contracts if c.buyer_id == buyer_id), None)
    if buyer_contract is None:
        raise TraceabilityForbiddenError("Este lote no está asignado a este comprador")

    compositions = [
        BatchCompositionItem(
            farm_id=comp.farm_id,
            farm_code=comp.farm.farm_code if comp.farm else None,
            contributed_kg=comp.contributed_kg,
            share_percentage=comp.share_percentage,
        )
        for comp in batch.compositions
    ]

    cuppings = [
        CuppingEvaluationItem(
            cupping_id=c.cupping_id,
            sca_total_score=c.sca_total_score,
            acidity_score=c.acidity_score,
            body_score=c.body_score,
        )
        for c in batch.cupping_evaluations
    ]

    buyer_name = (
        buyer_contract.buyer.buyer_name
        if buyer_contract.buyer
        else "Comprador Internacional"
    )

    return TraceabilityResponse(
        batch_id=batch.batch_id,
        batch_code=batch.batch_code,
        total_weight_kg=batch.total_weight_kg,
        traced_weight_kg=batch.traced_weight_kg,
        is_exportable=batch.is_exportable,
        is_certified=batch.is_certified,
        weighted_water_m3_ton=batch.weighted_water_m3_ton,
        buyer_id=buyer_id,
        buyer_name=buyer_name,
        created_at=batch.created_at,
        compositions=compositions,
        cupping_evaluations=cuppings,
    )
