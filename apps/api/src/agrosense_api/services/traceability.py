from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agrosense_api.models import BatchFarmComposition, ExportBatch
from agrosense_api.schemas.traceability import TraceabilityResponse


class TraceabilityForbiddenError(Exception):
    pass


async def get_batch_trace(
    session: AsyncSession, batch_code: str, buyer_id: UUID
) -> TraceabilityResponse | None:
    query = (
        select(ExportBatch)
        .options(
            selectinload(ExportBatch.farm),
            selectinload(ExportBatch.buyer),
            selectinload(ExportBatch.events),
            selectinload(ExportBatch.compositions).selectinload(BatchFarmComposition.farm),
        )
        .where(ExportBatch.batch_code == batch_code, ExportBatch.buyer_id == buyer_id)
    )
    batch = await session.scalar(query)
    if batch is None:
        existing_lot_id = await session.scalar(
            select(ExportBatch.id).where(ExportBatch.batch_code == batch_code)
        )
        if existing_lot_id is not None:
            raise TraceabilityForbiddenError("This export lot is not assigned to this buyer")
        return None

    farm = batch.farm or (batch.compositions[0].farm if batch.compositions else None)
    if farm is None:
        return None
    return TraceabilityResponse(
        batch_code=batch.batch_code,
        status=batch.status,
        farm_id=farm.id,
        farm_code=farm.code,
        farm_name=farm.name,
        buyer_name=batch.buyer.name if batch.buyer else None,
        created_at=batch.created_at,
        events=batch.events,
    )
