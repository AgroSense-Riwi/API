import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agrosense_api.models import (
    BatchFarmComposition,
    ExportBatch,
    Farm,
    FertilizerRecommendation,
    ForestReserve,
    TraceabilityEvent,
)
from agrosense_api.schemas.farms import FarmResponse
from agrosense_api.schemas.lots import (
    FarmContributionResponse,
    LotCertificateResponse,
    LotResponse,
)

ZERO = Decimal("0")
CERTIFIABLE_STATUSES = {"DRAFT", "EN_PROCESO"}


class LotCertificationError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _lot_response(batch: ExportBatch) -> LotResponse:
    contributions = [
        FarmContributionResponse(
            farm_id=composition.farm.id,
            farm_code=composition.farm.code,
            farm_name=composition.farm.name,
            contributed_weight_kg=composition.contributed_weight_kg,
            contribution_pct=(
                composition.contributed_weight_kg * Decimal("100") / batch.total_weight_kg
            ),
        )
        for composition in batch.compositions
    ]
    return LotResponse(
        id=batch.id,
        batch_code=batch.batch_code,
        status=batch.status,
        total_weight_kg=batch.total_weight_kg,
        cupping_score=batch.cupping_score,
        weighted_water_m3_ton=batch.weighted_water_m3_ton,
        avg_fertilizer_red_pct=batch.avg_fertilizer_red_pct,
        certified_at=batch.certified_at,
        is_exportable=batch.is_exportable,
        is_deforestation_free=batch.is_deforestation_free,
        created_at=batch.created_at,
        farms=contributions,
    )


async def get_lot(session: AsyncSession, lot_id: UUID) -> LotResponse | None:
    query = (
        select(ExportBatch)
        .where(ExportBatch.id == lot_id)
        .options(
            selectinload(ExportBatch.compositions).selectinload(BatchFarmComposition.farm),
            selectinload(ExportBatch.events),
        )
    )
    batch = await session.scalar(query)
    return _lot_response(batch) if batch is not None else None


async def get_farm(
    session: AsyncSession, farm_id: UUID, limit: int = 100, offset: int = 0
) -> FarmResponse | None:
    farm_query = select(
        Farm,
        func.ST_AsGeoJSON(Farm.location),
        func.ST_AsGeoJSON(Farm.boundary),
        select(func.coalesce(func.sum(BatchFarmComposition.contributed_weight_kg), ZERO))
        .where(BatchFarmComposition.farm_id == Farm.id)
        .scalar_subquery(),
    ).where(Farm.id == farm_id)
    farm_row = (await session.execute(farm_query)).one_or_none()
    if farm_row is None:
        return None

    farm, location, boundary, exported_weight = farm_row
    contributions_query = (
        select(ExportBatch, BatchFarmComposition.contributed_weight_kg)
        .join(BatchFarmComposition, BatchFarmComposition.batch_id == ExportBatch.id)
        .where(BatchFarmComposition.farm_id == farm_id)
        .order_by(ExportBatch.created_at.desc(), ExportBatch.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(contributions_query)).all()
    contributions: list[FarmContributionResponse] = []
    for batch, contributed_weight in rows:
        if batch.total_weight_kg > 0:
            contributions.append(
                FarmContributionResponse(
                    farm_id=farm.id,
                    farm_code=farm.code,
                    farm_name=farm.name,
                    contributed_weight_kg=contributed_weight,
                    contribution_pct=contributed_weight * Decimal("100") / batch.total_weight_kg,
                )
            )
    return FarmResponse(
        id=farm.id,
        code=farm.code,
        name=farm.name,
        location_geojson=json.loads(location) if location else None,
        boundary_geojson=json.loads(boundary) if boundary else None,
        water_usage_m3_ton=farm.water_usage_m3_ton,
        batch_contributions=contributions,
        total_exported_weight_kg=exported_weight,
    )


async def certify_lot(session: AsyncSession, lot_id: UUID) -> LotCertificateResponse:
    async with session.begin():
        batch = await session.scalar(
            select(ExportBatch).where(ExportBatch.id == lot_id).with_for_update()
        )
        if batch is None:
            raise LotCertificationError("Lot not found", status_code=404)
        if batch.status not in CERTIFIABLE_STATUSES:
            raise LotCertificationError(
                "Lot must be in DRAFT or EN_PROCESO status to be certified",
                status_code=409,
            )
        if batch.total_weight_kg <= 0:
            raise LotCertificationError("Lot net weight must be greater than zero")
        if batch.cupping_score is None or not Decimal("80") <= batch.cupping_score <= Decimal(
            "100"
        ):
            raise LotCertificationError(
                "Quality rule: SCA cupping score must be between 80 and 100"
            )

        traced_weight = await session.scalar(
            select(
                func.coalesce(
                    func.sum(BatchFarmComposition.contributed_weight_kg),
                    literal(ZERO),
                )
            ).where(BatchFarmComposition.batch_id == lot_id)
        )
        if traced_weight != batch.total_weight_kg:
            raise LotCertificationError(
                "RN-08: contributed farm weights must equal the lot net weight"
            )

        contributions_exist = await session.scalar(
            select(literal(True))
            .select_from(BatchFarmComposition)
            .where(BatchFarmComposition.batch_id == lot_id)
            .limit(1)
        )
        if not contributions_exist:
            raise LotCertificationError("RN-08: the lot has no registered farm origin")

        reserve_catalog_exists = await session.scalar(
            select(literal(True)).select_from(ForestReserve).limit(1)
        )
        if not reserve_catalog_exists:
            raise LotCertificationError("RN-09: forest reserve catalog is not configured")

        latest_saving = (
            select(FertilizerRecommendation.estimated_saving_pct)
            .where(FertilizerRecommendation.farm_id == Farm.id)
            .order_by(FertilizerRecommendation.generated_at.desc())
            .limit(1)
            .correlate(Farm)
            .scalar_subquery()
        )
        contribution_rows = (
            await session.execute(
                select(
                    BatchFarmComposition.contributed_weight_kg,
                    Farm.id,
                    Farm.code,
                    Farm.name,
                    Farm.boundary,
                    Farm.water_usage_m3_ton,
                    func.coalesce(latest_saving, literal(ZERO)),
                )
                .join(Farm, Farm.id == BatchFarmComposition.farm_id)
                .where(BatchFarmComposition.batch_id == lot_id)
                .with_for_update(of=[BatchFarmComposition, Farm])
            )
        ).all()

        missing_boundaries = [row.code for row in contribution_rows if row.boundary is None]
        if missing_boundaries:
            raise LotCertificationError(
                "RN-09: farm boundaries are required for all contributing farms"
            )
        missing_water_baselines = [
            row.code for row in contribution_rows if row.water_usage_m3_ton is None
        ]
        if missing_water_baselines:
            raise LotCertificationError(
                "RN-10: regional water-use baselines are required for all contributing farms"
            )

        overlapping_farm = await session.scalar(
            select(Farm.code)
            .join(BatchFarmComposition, BatchFarmComposition.farm_id == Farm.id)
            .join(
                ForestReserve,
                func.ST_Intersects(Farm.boundary, ForestReserve.boundary),
            )
            .where(BatchFarmComposition.batch_id == lot_id)
            .limit(1)
        )
        if overlapping_farm is not None:
            raise LotCertificationError(
                f"RN-09: contributing farm {overlapping_farm} intersects a protected reserve"
            )

        total_weight = Decimal(batch.total_weight_kg)
        water_weighted_sum = sum(
            (
                Decimal(row.contributed_weight_kg) * Decimal(row.water_usage_m3_ton)
                for row in contribution_rows
            ),
            start=ZERO,
        )
        fertilizer_weighted_sum = sum(
            (Decimal(row.contributed_weight_kg) * Decimal(row[6]) for row in contribution_rows),
            start=ZERO,
        )
        batch.weighted_water_m3_ton = water_weighted_sum / total_weight
        batch.avg_fertilizer_red_pct = fertilizer_weighted_sum / total_weight
        batch.status = "EXPORTABLE_CERTIFICADO"
        batch.is_exportable = True
        batch.is_deforestation_free = True
        batch.certified_at = datetime.now(UTC)
        session.add(
            TraceabilityEvent(
                batch_id=batch.id,
                event_type="LOT_CERTIFIED",
                description="Lot passed origin, reserve, and quality certification checks.",
            )
        )
        await session.flush()

        response = await session.scalar(
            select(ExportBatch)
            .where(ExportBatch.id == lot_id)
            .options(selectinload(ExportBatch.compositions).selectinload(BatchFarmComposition.farm))
        )
        if response is None:
            raise RuntimeError("Certified lot disappeared before transaction commit")
        return LotCertificateResponse.model_validate(_lot_response(response).model_dump())
