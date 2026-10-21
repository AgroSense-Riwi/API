from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BatchCompositionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    farm_id: UUID | None = None
    farm_code: str | None = None
    contributed_kg: Decimal
    share_percentage: Decimal


class CuppingEvaluationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cupping_id: UUID
    sca_total_score: Decimal
    acidity_score: Decimal | None = None
    body_score: Decimal | None = None


class BatchSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    batch_code: str
    total_weight_kg: Decimal
    traced_weight_kg: Decimal
    is_exportable: bool
    is_certified: bool
    weighted_water_m3_ton: Decimal | None = None
    created_at: datetime


class TraceabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    batch_code: str
    total_weight_kg: Decimal
    traced_weight_kg: Decimal
    is_exportable: bool
    is_certified: bool
    weighted_water_m3_ton: Decimal | None = None
    buyer_id: UUID
    buyer_name: str
    created_at: datetime
    compositions: list[BatchCompositionItem] = []
    cupping_evaluations: list[CuppingEvaluationItem] = []
