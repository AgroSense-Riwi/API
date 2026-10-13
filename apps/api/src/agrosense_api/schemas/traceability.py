from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TraceabilityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    description: str
    occurred_at: datetime


class TraceabilityResponse(BaseModel):
    batch_code: str
    status: str
    farm_id: UUID
    farm_code: str
    farm_name: str
    buyer_name: str | None
    created_at: datetime
    events: list[TraceabilityEventResponse]
