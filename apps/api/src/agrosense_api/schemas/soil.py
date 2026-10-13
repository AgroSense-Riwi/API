from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SoilRecommendationResponse(BaseModel):
    farm_id: UUID
    measured_at: datetime
    ph: float
    recommendations: list[str] = Field(
        description="Rule-based guidance; validate with an agronomist."
    )
