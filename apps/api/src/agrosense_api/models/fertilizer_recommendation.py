from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.farm import Farm
    from agrosense_api.models.farm_plot import FarmPlot
    from agrosense_api.models.soil_analysis import SoilAnalysis

NUTRIENT_EVALUATIONS = ("DEFICIENT", "OPTIMAL", "EXCESS_WARNING")
PH_EVALUATIONS = ("ACIDIC", "OPTIMAL", "ALKALINE")


class FertilizerRecommendation(Base):
    __tablename__ = "fertilizer_recommendations"
    __table_args__ = (
        CheckConstraint(
            "n_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_n_evaluation",
        ),
        CheckConstraint(
            "p_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_p_evaluation",
        ),
        CheckConstraint(
            "k_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_k_evaluation",
        ),
        CheckConstraint(
            "ph_evaluation IN ('ACIDIC', 'OPTIMAL', 'ALKALINE')",
            name="ck_fertilizer_recommendations_ph_evaluation",
        ),
        CheckConstraint(
            "recommended_n_kg_ha >= 0 AND recommended_p_kg_ha >= 0 AND recommended_k_kg_ha >= 0",
            name="ck_fertilizer_recommendations_doses_nonnegative",
        ),
        CheckConstraint(
            "estimated_saving_pct BETWEEN 0 AND 100",
            name="ck_fertilizer_recommendations_saving_range",
        ),
        Index(
            "ix_fertilizer_recommendations_farm_generated",
            "farm_id",
            desc("generated_at"),
        ),
        Index(
            "ix_fertilizer_recommendations_plot_generated",
            "plot_id",
            desc("generated_at"),
        ),
    )

    recommendation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    analysis_id: Mapped[UUID] = mapped_column(
        ForeignKey("soil_analyses.id", ondelete="CASCADE"), unique=True, index=True
    )
    plot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("farm_plots.plot_id", ondelete="SET NULL"), index=True, nullable=True
    )
    farm_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), index=True
    )
    n_evaluation: Mapped[str] = mapped_column(String(20), nullable=False)
    p_evaluation: Mapped[str] = mapped_column(String(20), nullable=False)
    k_evaluation: Mapped[str] = mapped_column(String(20), nullable=False)
    ph_evaluation: Mapped[str] = mapped_column(String(20), nullable=False)
    apply_fertilizer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recommended_n_kg_ha: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    recommended_p_kg_ha: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    recommended_k_kg_ha: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    estimated_saving_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    analysis: Mapped["SoilAnalysis"] = relationship(back_populates="fertilizer_recommendation")
    farm: Mapped["Farm"] = relationship(back_populates="fertilizer_recommendations")
    plot: Mapped["FarmPlot | None"] = relationship(back_populates="fertilizer_recommendations")
