from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class Department(BaseModel):
    """Catálogo departamental DANE (Sección 9)."""

    __tablename__ = "departments"

    department_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    dane_code: Mapped[str | None] = mapped_column(String(10), nullable=True)


class SensorType(BaseModel):
    """Catálogo de tipos de sensores IoT y rangos válidos (RN-02)."""

    __tablename__ = "sensor_types"

    sensor_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    min_valid_value: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    max_valid_value: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)


class IotSensor(BaseModel):
    """Inventario de sensores IoT instalados en las 300 fincas piloto (F5, RN-01, RN-02)."""

    __tablename__ = "iot_sensors"

    sensor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sensor_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", ondelete="CASCADE", name="fk_iot_sensors_farm_id"),
        nullable=False,
    )
    plot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farm_plots.plot_id", ondelete="SET NULL", name="fk_iot_sensors_plot_id"),
        nullable=True,
    )
    sensor_type_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sensor_types.sensor_type_id", name="fk_iot_sensors_sensor_type_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )
    installed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SensorAnomalyLog(BaseModel):
    """Bitácora de anomalías de sensores (SILENT >48h, OUT_OF_RANGE, UNRELIABLE - RN-02)."""

    __tablename__ = "sensor_anomalies_log"

    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sensor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "iot_sensors.sensor_id", ondelete="CASCADE", name="fk_sensor_anomalies_sensor_id"
        ),
        nullable=False,
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", ondelete="CASCADE", name="fk_sensor_anomalies_farm_id"),
        nullable=False,
    )
    anomaly_type: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[str | None] = mapped_column(String(300), nullable=True)


class CropNutrientReference(BaseModel):
    """Referencia agronómica de nutrientes NPK y clima (Fuente F1: Crop_recommendation.csv)."""

    __tablename__ = "crop_nutrient_references"

    ref_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crop_label: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    n_ref: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    p_ref: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    k_ref: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    temperature_ref: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    humidity_ref: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    ph_ref: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    rainfall_ref: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)


class SoilLabAnalysis(BaseModel):
    """Análisis de laboratorio de suelos mensual (Fuente F6)."""

    __tablename__ = "soil_lab_analyses"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", ondelete="CASCADE", name="fk_soil_lab_analyses_farm_id"),
        nullable=False,
    )
    plot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farm_plots.plot_id", ondelete="SET NULL", name="fk_soil_lab_analyses_plot_id"),
        nullable=True,
    )
    sample_date: Mapped[date] = mapped_column(Date, nullable=False)
    nitrogen_ppm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    phosphorus_ppm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    potassium_ppm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    ph_level: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    organic_matter_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)


class FertilizerRecommendation(BaseModel):
    """Recomendación de fertilización NPK por lote/parcela (RN-07)."""

    __tablename__ = "fertilizer_recommendations"

    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "soil_lab_analyses.analysis_id",
            ondelete="SET NULL",
            name="fk_fertilizer_recommendations_analysis_id",
        ),
        nullable=True,
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "farms.farm_id", ondelete="CASCADE", name="fk_fertilizer_recommendations_farm_id"
        ),
        nullable=False,
    )
    plot_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "farm_plots.plot_id",
            ondelete="SET NULL",
            name="fk_fertilizer_recommendations_plot_id",
        ),
        nullable=True,
    )
    crop_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    n_deficit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    p_deficit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    k_deficit_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    recommended_n_kg_ha: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    recommended_p_kg_ha: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    recommended_k_kg_ha: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    estimated_reduction_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    recommendation_notes: Mapped[str | None] = mapped_column(String(400), nullable=True)


class AgronomicAlert(BaseModel):
    """Alertas agronómicas generadas por Spark (RN-03 Roya, RN-04 Déficit, RN-05 Lluvia)."""

    __tablename__ = "agronomic_alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", ondelete="CASCADE", name="fk_agronomic_alerts_farm_id"),
        nullable=False,
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="OPEN",
        server_default=text("'OPEN'"),
    )
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    soil_moisture_mean_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    precip_accum_72h_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    temp_mean_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    rh_mean_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)


class AgronomistFieldVisit(BaseModel):
    """Registro de visitas técnicas de agrónomos y su resultado (RN-06)."""

    __tablename__ = "agronomist_field_visits"

    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("farms.farm_id", ondelete="CASCADE", name="fk_agronomist_visits_farm_id"),
        nullable=False,
    )
    agronomist_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL", name="fk_agronomist_visits_user_id"),
        nullable=True,
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "agronomic_alerts.alert_id",
            ondelete="SET NULL",
            name="fk_agronomist_visits_alert_id",
        ),
        nullable=True,
    )
    visit_date: Mapped[date] = mapped_column(Date, nullable=False)
    priority_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    visit_outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    alert_resolved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
