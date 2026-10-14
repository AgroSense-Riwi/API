from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import CheckConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.export_batch import ExportBatch
    from agrosense_api.models.farm_composition import BatchFarmComposition
    from agrosense_api.models.farm_plot import FarmPlot
    from agrosense_api.models.farm_visit import FarmVisit
    from agrosense_api.models.fertilizer_recommendation import FertilizerRecommendation
    from agrosense_api.models.soil_analysis import SoilAnalysis


class Farm(Base):
    __tablename__ = "farms"
    __table_args__ = (
        CheckConstraint(
            "water_usage_m3_ton IS NULL OR water_usage_m3_ton >= 0",
            name="ck_farms_water_usage_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    boundary: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False),
        nullable=True,
    )
    water_usage_m3_ton: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)

    export_batches: Mapped[list["ExportBatch"]] = relationship(back_populates="farm")
    batch_compositions: Mapped[list["BatchFarmComposition"]] = relationship(back_populates="farm")
    fertilizer_recommendations: Mapped[list["FertilizerRecommendation"]] = relationship(
        back_populates="farm"
    )
    plots: Mapped[list["FarmPlot"]] = relationship(back_populates="farm")
    soil_analyses: Mapped[list["SoilAnalysis"]] = relationship(back_populates="farm")
    visits: Mapped[list["FarmVisit"]] = relationship(back_populates="farm")
