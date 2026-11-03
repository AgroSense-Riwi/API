"""Add IoT sensors, agronomic alerts, field visits, soil lab analyses, and pilot farm flags.

Completes the 3FN operational schema from Proyecto_06_AgroSense_Agricultura_IoT (Sección 9):
- New tables: departments, sensor_types, iot_sensors, sensor_anomalies_log,
  crop_nutrient_references, soil_lab_analyses, fertilizer_recommendations,
  agronomic_alerts, agronomist_field_visits.
- Extended columns: farms (name, altitude_msl, total_area_ha, has_pilot_sensors,
  has_valid_coords), export_batches (avg_fertilizer_red_pct), international_buyers (country).

Revision ID: d4e8f1a2b3c4
Revises: f3b8c1d4a6e2
Create Date: 2026-11-03 07:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e8f1a2b3c4"
down_revision: str | None = "f3b8c1d4a6e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Additional columns on existing tables
    op.add_column("farms", sa.Column("name", sa.String(length=150), nullable=True))
    op.add_column("farms", sa.Column("altitude_msl", sa.Numeric(precision=7, scale=2), nullable=True))
    op.add_column("farms", sa.Column("total_area_ha", sa.Numeric(precision=8, scale=2), nullable=True))
    op.add_column(
        "farms",
        sa.Column(
            "has_pilot_sensors",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "farms",
        sa.Column(
            "has_valid_coords",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "export_batches",
        sa.Column("avg_fertilizer_red_pct", sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.add_column(
        "international_buyers",
        sa.Column("country", sa.String(length=80), nullable=True),
    )

    # 2. New operational tables
    op.create_table(
        "departments",
        sa.Column("department_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("dane_code", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("department_id", name="pk_departments"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )

    op.create_table(
        "sensor_types",
        sa.Column("sensor_type_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type_code", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("min_valid_value", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("max_valid_value", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sensor_type_id", name="pk_sensor_types"),
        sa.UniqueConstraint("type_code", name="uq_sensor_types_type_code"),
    )

    op.create_table(
        "iot_sensors",
        sa.Column(
            "sensor_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sensor_code", sa.String(length=80), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sensor_type_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column("installed_at", sa.Date(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_iot_sensors_farm_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plot_id"],
            ["farm_plots.plot_id"],
            name="fk_iot_sensors_plot_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sensor_type_id"],
            ["sensor_types.sensor_type_id"],
            name="fk_iot_sensors_sensor_type_id",
        ),
        sa.PrimaryKeyConstraint("sensor_id", name="pk_iot_sensors"),
        sa.UniqueConstraint("sensor_code", name="uq_iot_sensors_sensor_code"),
    )
    op.create_index("ix_iot_sensors_sensor_code", "iot_sensors", ["sensor_code"], unique=True)

    op.create_table(
        "sensor_anomalies_log",
        sa.Column(
            "anomaly_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sensor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anomaly_type", sa.String(length=40), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_sensor_anomalies_farm_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sensor_id"],
            ["iot_sensors.sensor_id"],
            name="fk_sensor_anomalies_sensor_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("anomaly_id", name="pk_sensor_anomalies_log"),
    )

    op.create_table(
        "crop_nutrient_references",
        sa.Column("ref_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("crop_label", sa.String(length=80), nullable=False),
        sa.Column("n_ref", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("p_ref", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("k_ref", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("temperature_ref", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("humidity_ref", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("ph_ref", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("rainfall_ref", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("ref_id", name="pk_crop_nutrient_references"),
    )
    op.create_index(
        "ix_crop_nutrient_references_crop_label",
        "crop_nutrient_references",
        ["crop_label"],
        unique=False,
    )

    op.create_table(
        "soil_lab_analyses",
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sample_date", sa.Date(), nullable=False),
        sa.Column("nitrogen_ppm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("phosphorus_ppm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("potassium_ppm", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("ph_level", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("organic_matter_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_soil_lab_analyses_farm_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plot_id"],
            ["farm_plots.plot_id"],
            name="fk_soil_lab_analyses_plot_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("analysis_id", name="pk_soil_lab_analyses"),
    )

    op.create_table(
        "fertilizer_recommendations",
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crop_stage", sa.String(length=50), nullable=False),
        sa.Column("n_deficit_pct", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("p_deficit_pct", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("k_deficit_pct", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("recommended_n_kg_ha", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("recommended_p_kg_ha", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("recommended_k_kg_ha", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("estimated_reduction_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("recommendation_notes", sa.String(length=400), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["soil_lab_analyses.analysis_id"],
            name="fk_fertilizer_recommendations_analysis_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_fertilizer_recommendations_farm_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plot_id"],
            ["farm_plots.plot_id"],
            name="fk_fertilizer_recommendations_plot_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("recommendation_id", name="pk_fertilizer_recommendations"),
    )

    op.create_table(
        "agronomic_alerts",
        sa.Column(
            "alert_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column(
            "alert_status",
            sa.String(length=30),
            server_default=sa.text("'OPEN'"),
            nullable=False,
        ),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column("soil_moisture_mean_pct", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("precip_accum_72h_mm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("temp_mean_c", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("rh_mean_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_agronomic_alerts_farm_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alert_id", name="pk_agronomic_alerts"),
    )

    op.create_table(
        "agronomist_field_visits",
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agronomist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("priority_score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("visit_outcome", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "alert_resolved",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agronomist_id"],
            ["users.user_id"],
            name="fk_agronomist_visits_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["agronomic_alerts.alert_id"],
            name="fk_agronomist_visits_alert_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.farm_id"],
            name="fk_agronomist_visits_farm_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("visit_id", name="pk_agronomist_field_visits"),
    )


def downgrade() -> None:
    op.drop_table("agronomist_field_visits")
    op.drop_table("agronomic_alerts")
    op.drop_table("fertilizer_recommendations")
    op.drop_table("soil_lab_analyses")
    op.drop_index("ix_crop_nutrient_references_crop_label", table_name="crop_nutrient_references")
    op.drop_table("crop_nutrient_references")
    op.drop_table("sensor_anomalies_log")
    op.drop_index("ix_iot_sensors_sensor_code", table_name="iot_sensors")
    op.drop_table("iot_sensors")
    op.drop_table("sensor_types")
    op.drop_table("departments")

    op.drop_column("international_buyers", "country")
    op.drop_column("export_batches", "avg_fertilizer_red_pct")
    op.drop_column("farms", "has_valid_coords")
    op.drop_column("farms", "has_pilot_sensors")
    op.drop_column("farms", "total_area_ha")
    op.drop_column("farms", "altitude_msl")
    op.drop_column("farms", "name")
