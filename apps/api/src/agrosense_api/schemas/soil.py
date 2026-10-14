from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SoilRecommendationResponse(BaseModel):
    analysis_id: UUID
    farm_id: UUID
    plot_id: UUID | None
    measured_at: datetime
    ph: float
    n_evaluation: Literal["DEFICIENT", "OPTIMAL", "EXCESS_WARNING"]
    p_evaluation: Literal["DEFICIENT", "OPTIMAL", "EXCESS_WARNING"]
    k_evaluation: Literal["DEFICIENT", "OPTIMAL", "EXCESS_WARNING"]
    ph_evaluation: Literal["ACIDIC", "OPTIMAL", "ALKALINE"]
    apply_fertilizer: bool
    recommended_n_kg_ha: Decimal
    recommended_p_kg_ha: Decimal
    recommended_k_kg_ha: Decimal
    estimated_saving_pct: Decimal = Field(ge=0, le=100)
    recommendations: list[str] = Field(
        description="RN-07 guidance based on the selected crop reference."
    )
