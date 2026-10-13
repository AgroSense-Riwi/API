from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base

if TYPE_CHECKING:
    from agrosense_api.models.export_batch import ExportBatch


class Buyer(Base):
    __tablename__ = "buyers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    export_batches: Mapped[list["ExportBatch"]] = relationship(back_populates="buyer")
