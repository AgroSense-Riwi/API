"""Endpoints operativos CRUD, filtrado por Query params, certificación RN-08..10 y verificación pública (Tema #1, E-05, E-16)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.api.v1.dependencies import get_operator_db, require_roles
from agrosense_api.models.batch import ExportBatch
from agrosense_api.models.farm import Farm
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.services.certificate_service import PublicCertificateValidator

router = APIRouter(tags=["Operaciones & Certificación"])


class FarmSummaryItem(BaseModel):
    farm_id: UUID
    farm_code: str
    municipality_id: int
    latitude: float
    longitude: float
    cultivated_area_ha: float
    water_usage_m3_ton: float | None = None
    overlaps_reserve: bool = False
    has_pilot_sensors: bool = False


class FarmUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    water_usage_m3_ton: float | None = Field(default=None, ge=0, le=50)
    has_pilot_sensors: bool | None = None


class LotSummaryItem(BaseModel):
    batch_id: UUID
    batch_code: str
    total_weight_kg: float
    traced_weight_kg: float
    is_exportable: bool
    is_certified: bool
    weighted_water_m3_ton: float | None = None


class CertificateVerificationResponse(BaseModel):
    is_valid: bool
    status: str
    batch_code: str | None = None
    issuer: str | None = None
    computed_hash: str | None = None
    stored_hash: str | None = None
    message: str | None = None


@router.get(
    "/farms",
    response_model=list[FarmSummaryItem],
    summary="Listar fincas con filtros Query (Tema #1)",
)
async def list_farms(
    session: Annotated[
        AsyncSession,
        Depends(get_operator_db("ADMIN", "AGRONOMIST", "QUALITY_CHIEF", "SUSTAINABILITY_COMMITTEE")),
    ],
    municipality_id: Annotated[int | None, Query(ge=1, description="Filtrar por municipio")] = None,
    overlaps_reserve: Annotated[
        bool | None, Query(description="Filtrar por superposición con reserva forestal (RN-09)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Máximo de registros")] = 50,
    offset: Annotated[int, Query(ge=0, description="Desplazamiento de paginación")] = 0,
) -> list[FarmSummaryItem]:
    stmt = select(Farm).order_by(Farm.farm_code).limit(limit).offset(offset)
    if municipality_id is not None:
        stmt = stmt.where(Farm.municipality_id == municipality_id)
    if overlaps_reserve is not None:
        stmt = stmt.where(Farm.overlaps_reserve == overlaps_reserve)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        FarmSummaryItem(
            farm_id=f.farm_id,
            farm_code=f.farm_code,
            municipality_id=f.municipality_id,
            latitude=f.latitude,
            longitude=f.longitude,
            cultivated_area_ha=f.cultivated_area_ha,
            water_usage_m3_ton=f.water_usage_m3_ton,
            overlaps_reserve=f.overlaps_reserve,
            has_pilot_sensors=getattr(f, "has_pilot_sensors", False),
        )
        for f in rows
    ]


@router.get(
    "/farms/{farm_id}",
    response_model=FarmSummaryItem,
    summary="Consultar detalle de finca por UUID o código (RNF-03)",
)
async def get_farm_detail(
    farm_id: Annotated[str, Path(min_length=1, max_length=64)],
    session: Annotated[
        AsyncSession,
        Depends(get_operator_db("ADMIN", "AGRONOMIST", "QUALITY_CHIEF", "SUSTAINABILITY_COMMITTEE")),
    ],
) -> FarmSummaryItem:
    try:
        parsed_uuid = UUID(farm_id)
        stmt = select(Farm).where(Farm.farm_id == parsed_uuid)
    except ValueError:
        stmt = select(Farm).where(Farm.farm_code == farm_id)
    farm = (await session.execute(stmt)).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farm not found")
    return FarmSummaryItem(
        farm_id=farm.farm_id,
        farm_code=farm.farm_code,
        municipality_id=farm.municipality_id,
        latitude=farm.latitude,
        longitude=farm.longitude,
        cultivated_area_ha=farm.cultivated_area_ha,
        water_usage_m3_ton=farm.water_usage_m3_ton,
        overlaps_reserve=farm.overlaps_reserve,
        has_pilot_sensors=getattr(farm, "has_pilot_sensors", False),
    )


@router.patch(
    "/farms/{farm_id}",
    response_model=FarmSummaryItem,
    summary="Actualizar indicadores operativos de finca (PATCH - Tema #1)",
)
async def update_farm_indicators(
    farm_id: Annotated[UUID, Path(description="UUID de la finca")],
    payload: FarmUpdatePayload,
    session: Annotated[AsyncSession, Depends(get_operator_db("ADMIN", "AGRONOMIST"))],
) -> FarmSummaryItem:
    farm = (await session.execute(select(Farm).where(Farm.farm_id == farm_id))).scalar_one_or_none()
    if farm is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farm not found")
    if payload.water_usage_m3_ton is not None:
        farm.water_usage_m3_ton = payload.water_usage_m3_ton
    if payload.has_pilot_sensors is not None and hasattr(farm, "has_pilot_sensors"):
        farm.has_pilot_sensors = payload.has_pilot_sensors
    await session.commit()
    await session.refresh(farm)
    return FarmSummaryItem(
        farm_id=farm.farm_id,
        farm_code=farm.farm_code,
        municipality_id=farm.municipality_id,
        latitude=farm.latitude,
        longitude=farm.longitude,
        cultivated_area_ha=farm.cultivated_area_ha,
        water_usage_m3_ton=farm.water_usage_m3_ton,
        overlaps_reserve=farm.overlaps_reserve,
        has_pilot_sensors=getattr(farm, "has_pilot_sensors", False),
    )


@router.get(
    "/lots",
    response_model=list[LotSummaryItem],
    summary="Listar lotes de exportación con filtros Query (Tema #1)",
)
async def list_lots(
    session: Annotated[
        AsyncSession,
        Depends(get_operator_db("ADMIN", "QUALITY_CHIEF", "SUSTAINABILITY_COMMITTEE")),
    ],
    certified_only: Annotated[bool | None, Query(description="Filtrar por lotes certificados")] = None,
    exportable_only: Annotated[bool | None, Query(description="Filtrar por lotes exportables")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LotSummaryItem]:
    stmt = select(ExportBatch).order_by(ExportBatch.batch_code).limit(limit).offset(offset)
    if certified_only is not None:
        stmt = stmt.where(ExportBatch.is_certified == certified_only)
    if exportable_only is not None:
        stmt = stmt.where(ExportBatch.is_exportable == exportable_only)
    batches = (await session.execute(stmt)).scalars().all()
    return [
        LotSummaryItem(
            batch_id=b.batch_id,
            batch_code=b.batch_code,
            total_weight_kg=b.total_weight_kg,
            traced_weight_kg=b.traced_weight_kg,
            is_exportable=b.is_exportable,
            is_certified=b.is_certified,
            weighted_water_m3_ton=b.weighted_water_m3_ton,
        )
        for b in batches
    ]


@router.get(
    "/lots/{lot_id}",
    response_model=LotSummaryItem,
    summary="Consultar lote por UUID o código (RNF-03)",
)
async def get_lot_detail(
    lot_id: Annotated[str, Path(min_length=1, max_length=100)],
    session: Annotated[
        AsyncSession,
        Depends(get_operator_db("ADMIN", "QUALITY_CHIEF", "SUSTAINABILITY_COMMITTEE")),
    ],
) -> LotSummaryItem:
    try:
        parsed_uuid = UUID(lot_id)
        stmt = select(ExportBatch).where(ExportBatch.batch_id == parsed_uuid)
    except ValueError:
        stmt = select(ExportBatch).where(ExportBatch.batch_code == lot_id)
    batch = (await session.execute(stmt)).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    return LotSummaryItem(
        batch_id=batch.batch_id,
        batch_code=batch.batch_code,
        total_weight_kg=batch.total_weight_kg,
        traced_weight_kg=batch.traced_weight_kg,
        is_exportable=batch.is_exportable,
        is_certified=batch.is_certified,
        weighted_water_m3_ton=batch.weighted_water_m3_ton,
    )


@router.delete(
    "/lots/{batch_code}/certification",
    status_code=status.HTTP_200_OK,
    summary="Revocar certificación de lote (DELETE - Tema #1)",
)
async def revoke_lot_certification(
    batch_code: Annotated[str, Path(min_length=1, max_length=100)],
    session: Annotated[AsyncSession, Depends(get_operator_db("ADMIN", "QUALITY_CHIEF"))],
    _user: Annotated[AuthenticatedUser, Depends(require_roles("ADMIN", "QUALITY_CHIEF"))],
) -> dict[str, Any]:
    stmt = select(ExportBatch).where(ExportBatch.batch_code == batch_code)
    batch = (await session.execute(stmt)).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lot not found")
    batch.is_certified = False
    await session.commit()
    return {"batch_code": batch.batch_code, "is_certified": False, "status": "REVOKED"}


@router.post(
    "/certificates/verify",
    response_model=CertificateVerificationResponse,
    summary="Verificar autenticidad criptográfica SHA-256 de un certificado digital (SCRUM-168 / E-16)",
)
async def verify_digital_certificate(
    certificate_payload: Annotated[dict[str, Any], Body(description="JSON del certificado digital")],
) -> CertificateVerificationResponse:
    result = PublicCertificateValidator.verify_certificate_integrity(certificate_payload)
    return CertificateVerificationResponse(**result)
