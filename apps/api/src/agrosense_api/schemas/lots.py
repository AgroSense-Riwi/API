from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FarmContributionResponse(BaseModel):
    farm_id: UUID
    farm_code: str
    farm_name: str
    contributed_weight_kg: Decimal = Field(gt=0)
    contribution_pct: Decimal = Field(ge=0)


class LotResponse(BaseModel):
    id: UUID
    batch_code: str
    status: str
    total_weight_kg: Decimal = Field(gt=0)
    cupping_score: Decimal | None
    weighted_water_m3_ton: Decimal | None
    avg_fertilizer_red_pct: Decimal | None
    certified_at: datetime | None
    is_exportable: bool
    is_deforestation_free: bool
    created_at: datetime
    farms: list[FarmContributionResponse]


class LotCertifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_id: UUID


class LotCertificateResponse(LotResponse):
    status: str = "CERTIFIED"
    weighted_water_m3_ton: Decimal = Field(ge=0)
    avg_fertilizer_red_pct: Decimal = Field(ge=-100, le=100)
    certified_at: datetime
    is_exportable: bool = True
    is_deforestation_free: bool = True
