from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.buyer import Buyer
    from agrosense_api.models.farm import Farm
    from agrosense_api.models.farm_composition import BatchFarmComposition
    from agrosense_api.models.traceability_event import TraceabilityEvent


class ExportBatch(Base):
    __tablename__ = "export_batches"
    __table_args__ = (
        CheckConstraint("total_weight_kg > 0", name="ck_export_batches_weight_positive"),
        CheckConstraint(
            "cupping_score IS NULL OR cupping_score BETWEEN 0 AND 100",
            name="ck_export_batches_cupping_score_range",
        ),
        CheckConstraint(
            "weighted_water_m3_ton IS NULL OR weighted_water_m3_ton >= 0",
            name="ck_export_batches_water_nonnegative",
        ),
        CheckConstraint(
            "avg_fertilizer_red_pct IS NULL OR avg_fertilizer_red_pct BETWEEN -100 AND 100",
            name="ck_export_batches_fertilizer_reduction_range",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'EN_PROCESO', 'CERTIFIED', "
            "'EXPORTABLE_CERTIFICADO', 'REJECTED', 'RECHAZADO')",
            name="ck_export_batches_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    batch_code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    farm_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("farms.id"), nullable=True, index=True
    )
    buyer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("buyers.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="EN_PROCESO", index=True)
    total_weight_kg: Mapped[float] = mapped_column(Numeric(10, 2))
    cupping_score: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    weighted_water_m3_ton: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    avg_fertilizer_red_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    certified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_exportable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deforestation_free: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    farm: Mapped["Farm"] = relationship(back_populates="export_batches")
    compositions: Mapped[list["BatchFarmComposition"]] = relationship(
        back_populates="batch", order_by="BatchFarmComposition.farm_id"
    )
    buyer: Mapped["Buyer | None"] = relationship(back_populates="export_batches")
    events: Mapped[list["TraceabilityEvent"]] = relationship(
        back_populates="batch", order_by="TraceabilityEvent.occurred_at"
    )
