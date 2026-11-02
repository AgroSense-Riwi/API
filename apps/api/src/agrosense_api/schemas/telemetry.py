from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class TelemetryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    farm_id: UUID
    sensor_id: str = Field(min_length=1, max_length=100)
    metric: str = Field(min_length=1, max_length=100)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=30)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TelemetryAccepted(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    status: Literal["queued"] = "queued"
