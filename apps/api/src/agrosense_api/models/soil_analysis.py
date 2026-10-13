from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.farm import Farm


class SoilAnalysis(Base):
    __tablename__ = "soil_analyses"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    farm_id: Mapped[UUID] = mapped_column(ForeignKey("farms.id", ondelete="CASCADE"), index=True)
    ph: Mapped[float] = mapped_column(Float)
    nitrogen_mg_kg: Mapped[float] = mapped_column(Float)
    phosphorus_mg_kg: Mapped[float] = mapped_column(Float)
    potassium_mg_kg: Mapped[float] = mapped_column(Float)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    farm: Mapped["Farm"] = relationship(back_populates="soil_analyses")
