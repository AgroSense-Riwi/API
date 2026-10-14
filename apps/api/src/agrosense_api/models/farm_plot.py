from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.farm import Farm
    from agrosense_api.models.fertilizer_recommendation import FertilizerRecommendation
    from agrosense_api.models.soil_analysis import SoilAnalysis


class FarmPlot(Base):
    __tablename__ = "farm_plots"

    plot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    farm_id: Mapped[UUID] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    variety_id: Mapped[int] = mapped_column(
        ForeignKey("coffee_varieties.variety_id"), nullable=False, index=True
    )
    growth_stage: Mapped[str] = mapped_column(String(50), nullable=False, default="VEGETATIVE")
    plot_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plot_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    cultivated_area_ha: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    plot_area_ha: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    tree_density_per_ha: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crop_age_years: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    polygon_geojson: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    farm: Mapped["Farm"] = relationship(back_populates="plots")
    soil_analyses: Mapped[list["SoilAnalysis"]] = relationship(back_populates="plot")
    fertilizer_recommendations: Mapped[list["FertilizerRecommendation"]] = relationship(
        back_populates="plot"
    )
