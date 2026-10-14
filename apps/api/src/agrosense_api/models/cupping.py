from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .batch import ExportBatch


class User(BaseModel):
    """Usuarios internos del sistema (ej. QUALITY_CHIEF, ADMIN, OPERATOR)."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Relaciones
    evaluations: Mapped[list[CuppingEvaluation]] = relationship(
        "CuppingEvaluation",
        back_populates="quality_chief",
    )

    def __repr__(self) -> str:
        return f"<User(user_id={self.user_id}, full_name='{self.full_name}', role='{self.role}')>"


class CuppingEvaluation(BaseModel):
    """Evaluación sensorial de catación bajo protocolo SCA para un lote de exportación."""

    __tablename__ = "cupping_evaluations"
    __table_args__ = (
        CheckConstraint(
            "sca_total_score BETWEEN 0.00 AND 100.00",
            name="ck_cupping_evaluations_sca_score_range",
        ),
    )

    cupping_id: Mapped[uuid.UUID] = mapped_column(
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
            name="fk_cupping_evaluations_batch_id",
        ),
        nullable=False,
    )
    quality_chief_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.user_id",
            name="fk_cupping_evaluations_quality_chief_id",
        ),
        nullable=False,
    )
    sca_total_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
    )
    acidity_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )
    body_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )
    sensory_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relaciones
    batch: Mapped[ExportBatch] = relationship(
        "ExportBatch",
        back_populates="cupping_evaluations",
    )
    quality_chief: Mapped[User] = relationship(
        "User",
        back_populates="evaluations",
    )

    def __repr__(self) -> str:
        return (
            f"<CuppingEvaluation(cupping_id={self.cupping_id}, "
            f"sca_total_score={self.sca_total_score})>"
        )
