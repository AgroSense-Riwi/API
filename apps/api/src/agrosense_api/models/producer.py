from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .farm import Farm


class Producer(BaseModel):
    """Entidad que representa a un productor cafetero registrado."""

    __tablename__ = "producers"

    producer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    document_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )
    phone_number: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    cooperative_join_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Relaciones
    farms: Mapped[list[Farm]] = relationship(
        "Farm",
        back_populates="producer",
    )

    def __repr__(self) -> str:
        return (
            f"<Producer(producer_id={self.producer_id}, document_number='{self.document_number}')>"
        )
