from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .buyer import BuyerBatchContract
    from .cupping import CuppingEvaluation
    from .farm import Farm
    from .harvest import HarvestDelivery


class ExportBatch(BaseModel):
    """Lote consolidado de café para exportación."""

    __tablename__ = "export_batches"
    __table_args__ = (
        CheckConstraint(
            "total_weight_kg > 0",
            name="ck_export_batches_total_weight_positive",
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    batch_code: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    total_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )
    traced_weight_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    is_exportable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_certified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    weighted_water_m3_ton: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2),
        nullable=True,
    )

    # Relaciones
    compositions: Mapped[list[BatchFarmComposition]] = relationship(
        "BatchFarmComposition",
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    cupping_evaluations: Mapped[list[CuppingEvaluation]] = relationship(
        "CuppingEvaluation",
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    contracts: Mapped[list[BuyerBatchContract]] = relationship(
        "BuyerBatchContract",
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<ExportBatch(batch_id={self.batch_id}, batch_code='{self.batch_code}')>"


class BatchFarmComposition(BaseModel):
    """
    Desglose de trazabilidad que vincula un lote de exportación
    con las entregas de cosecha y fincas de origen (RN-08).
    """

    __tablename__ = "batch_farm_compositions"
    __table_args__ = (
        CheckConstraint(
            "contributed_kg > 0",
            name="ck_batch_compositions_contributed_kg_positive",
        ),
        CheckConstraint(
            "share_percentage BETWEEN 0.00 AND 100.00",
            name="ck_batch_compositions_share_percentage_range",
        ),
    )

    composition_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "export_batches.batch_id",
            ondelete="CASCADE",
            name="fk_batch_compositions_batch_id",
        ),
        nullable=False,
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "harvest_deliveries.delivery_id",
            name="fk_batch_compositions_delivery_id",
        ),
        nullable=False,
    )
    farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "farms.farm_id",
            name="fk_batch_compositions_farm_id",
        ),
        nullable=True,
    )
    contributed_kg: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )
    share_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )

    # Relaciones
    batch: Mapped[ExportBatch] = relationship(
        "ExportBatch",
        back_populates="compositions",
    )
    delivery: Mapped[HarvestDelivery] = relationship(
        "HarvestDelivery",
        back_populates="compositions",
    )
    farm: Mapped[Farm | None] = relationship(
        "Farm",
        back_populates="batch_compositions",
    )

    def __repr__(self) -> str:
        return (
            f"<BatchFarmComposition(composition_id={self.composition_id}, "
            f"contributed_kg={self.contributed_kg}, share_percentage={self.share_percentage})>"
        )
