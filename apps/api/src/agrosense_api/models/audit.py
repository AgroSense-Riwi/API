import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BuyerApiAuditLog(Base):
    """Auditoría de consultas de compradores a la API de trazabilidad (CA-09, RNF-05).

    Tabla de solo inserción (sin ``updated_at``). La API escribe con la función
    ``log_buyer_api_access`` (migración ``a9c4e6b2d8f1``), no con este modelo.
    """

    __tablename__ = "buyer_api_audit_logs"

    audit_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("international_buyers.buyer_id"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("export_batches.batch_id"),
        nullable=True,
    )
    http_status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<BuyerApiAuditLog(audit_id={self.audit_id}, buyer_id={self.buyer_id}, "
            f"http_status_code={self.http_status_code})>"
        )
