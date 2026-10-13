from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, desc, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.farm import Farm


class FertilizerRecommendation(Base):
    __tablename__ = "fertilizer_recommendations"
    __table_args__ = (
        CheckConstraint(
            "estimated_saving_pct BETWEEN -100 AND 100",
            name="ck_fertilizer_recommendations_saving_range",
        ),
        Index(
            "ix_fertilizer_recommendations_farm_generated",
            "farm_id",
            desc("generated_at"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    farm_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), index=True
    )
    estimated_saving_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0")
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    farm: Mapped["Farm"] = relationship(back_populates="fertilizer_recommendations")
