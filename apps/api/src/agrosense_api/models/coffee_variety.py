from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from agrosense_api.db.base import Base


class CoffeeVariety(Base):
    __tablename__ = "coffee_varieties"

    variety_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    rust_resistance_level: Mapped[str] = mapped_column(String(50), nullable=False)
    rust_susceptibility: Mapped[str] = mapped_column(String(50), nullable=False)
    optimal_temp_min_c: Mapped[float] = mapped_column(Float, nullable=False)
    optimal_temp_max_c: Mapped[float] = mapped_column(Float, nullable=False)
