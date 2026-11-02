from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .farm import Farm


class Municipality(BaseModel):
    """Entidad que representa un municipio de ubicación de fincas."""

    __tablename__ = "municipalities"

    municipality_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    # Relaciones
    farms: Mapped[list[Farm]] = relationship(
        "Farm",
        back_populates="municipality",
    )

    def __repr__(self) -> str:
        return f"<Municipality(municipality_id={self.municipality_id}, name='{self.name}')>"
