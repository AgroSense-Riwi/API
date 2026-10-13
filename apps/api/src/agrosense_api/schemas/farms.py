from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from agrosense_api.schemas.lots import FarmContributionResponse


class FarmResponse(BaseModel):
    id: UUID
    code: str
    name: str
    location_geojson: dict[str, object] | None
    boundary_geojson: dict[str, object] | None
    water_usage_m3_ton: Decimal | None
    batch_contributions: list[FarmContributionResponse]
    total_exported_weight_kg: Decimal = Field(ge=0)
