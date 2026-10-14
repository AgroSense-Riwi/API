from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel

if TYPE_CHECKING:
    from .batch import ExportBatch


class InternationalBuyer(BaseModel):
    """Comprador internacional autorizado para adquirir lotes de exportación."""

    __tablename__ = "international_buyers"

    buyer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    client_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    buyer_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # Relaciones
    contracts: Mapped[list[BuyerBatchContract]] = relationship(
        "BuyerBatchContract",
        back_populates="buyer",
    )

    def __repr__(self) -> str:
        return f"<InternationalBuyer(buyer_id={self.buyer_id}, client_id='{self.client_id}')>"


class BuyerBatchContract(BaseModel):
    """
    Tabla de asociación contractual entre compradores internacionales y lotes (RN-11).
    Restringe la eliminación de compradores con contratos asociados (RESTRICT).
    """

    __tablename__ = "buyer_batch_contracts"

    contract_id: Mapped[uuid.UUID] = mapped_column(
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
            name="fk_buyer_batch_contracts_batch_id",
        ),
        nullable=False,
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "international_buyers.buyer_id",
            ondelete="RESTRICT",
            name="fk_buyer_batch_contracts_buyer_id",
        ),
        nullable=False,
    )

    # Relaciones
    batch: Mapped[ExportBatch] = relationship(
        "ExportBatch",
        back_populates="contracts",
    )
    buyer: Mapped[InternationalBuyer] = relationship(
        "InternationalBuyer",
        back_populates="contracts",
    )

    def __repr__(self) -> str:
        return (
            f"<BuyerBatchContract(contract_id={self.contract_id}, "
            f"batch_id={self.batch_id}, buyer_id={self.buyer_id})>"
        )
