from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from geoalchemy2.elements import WKBElement
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from agrosense_api.db.base import Base


class ForestReserve(Base):
    __tablename__ = "forest_reserves"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    boundary: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False)
    )
