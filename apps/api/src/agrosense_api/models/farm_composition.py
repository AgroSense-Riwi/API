from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.export_batch import ExportBatch
    from agrosense_api.models.farm import Farm


class BatchFarmComposition(Base):
    __tablename__ = "batch_farm_compositions"
    __table_args__ = (
        CheckConstraint(
            "contributed_weight_kg > 0",
            name="ck_batch_farm_compositions_weight_positive",
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("export_batches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    farm_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("farms.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    contributed_weight_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    batch: Mapped["ExportBatch"] = relationship(back_populates="compositions")
    farm: Mapped["Farm"] = relationship(back_populates="batch_compositions")
