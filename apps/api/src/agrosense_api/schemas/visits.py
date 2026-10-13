from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PriorityVisitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    farm_id: UUID
    reason: str
    priority_score: float = Field(ge=0, le=100)
    due_at: datetime
    farm_code: str
    farm_name: str
