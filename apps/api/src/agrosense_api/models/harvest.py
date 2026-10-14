from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .batch import BatchFarmComposition
    from .farm import Farm, FarmPlot


class HarvestDelivery(BaseModel):
    """Registro de entregas de cosecha realizadas por una finca/parcela."""

    __tablename__ = "harvest_deliveries"
    __table_args__ = (
        CheckConstraint(
            "delivered_weight_kg > 0",
            name="ck_harvest_deliveries_weight_positive",
        ),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", name="fk_harvest_deliveries_farm_id"),
        nullable=True,
    )
    plot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farm_plots.plot_id", name="fk_harvest_deliveries_plot_id"),
        nullable=True,
    )
    delivery_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    delivered_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    # Relaciones
    farm: Mapped[Farm | None] = relationship(
        "Farm",
        back_populates="harvest_deliveries",
    )
    plot: Mapped[FarmPlot | None] = relationship(
        "FarmPlot",
        back_populates="harvest_deliveries",
    )
    compositions: Mapped[list[BatchFarmComposition]] = relationship(
        "BatchFarmComposition",
        back_populates="delivery",
    )

    def __repr__(self) -> str:
        return (
            f"<HarvestDelivery(delivery_id={self.delivery_id}, "
            f"delivered_weight_kg={self.delivered_weight_kg})>"
        )
