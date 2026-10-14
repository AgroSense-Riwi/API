from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agrosense_api.db.base import Base


class CropNutrientReference(Base):
    __tablename__ = "crop_nutrient_references"
    __table_args__ = (
        UniqueConstraint(
            "variety_id",
            "growth_stage",
            name="uq_crop_nutrient_references_variety_stage",
        ),
    )

    reference_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variety_id: Mapped[int] = mapped_column(
        ForeignKey("coffee_varieties.variety_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    crop_label: Mapped[str] = mapped_column(String(100), nullable=False)
    growth_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    target_ph_min: Mapped[float] = mapped_column(Float, nullable=False)
    target_ph_max: Mapped[float] = mapped_column(Float, nullable=False)
    ph_min: Mapped[float] = mapped_column(Float, nullable=False)
    ph_max: Mapped[float] = mapped_column(Float, nullable=False)
    n_min_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    n_max_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    p_min_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    p_max_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    k_min_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    k_max_mg_kg: Mapped[float] = mapped_column(Float, nullable=False)
    req_nitrogen_kg_ha: Mapped[float] = mapped_column(Float, nullable=False)
    req_phosphorus_kg_ha: Mapped[float] = mapped_column(Float, nullable=False)
    req_potassium_kg_ha: Mapped[float] = mapped_column(Float, nullable=False)
