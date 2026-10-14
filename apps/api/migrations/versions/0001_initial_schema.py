"""Create initial AgroSense API schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "buyers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("country", sa.String(length=100), nullable=True),
    )
    op.create_table(
        "farms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "boundary",
            Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("water_usage_m3_ton", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "location",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.CheckConstraint(
            "water_usage_m3_ton IS NULL OR water_usage_m3_ton >= 0",
            name="ck_farms_water_usage_nonnegative",
        ),
    )
    op.create_index("ix_farms_code", "farms", ["code"], unique=True)
    op.create_index("ix_farms_boundary", "farms", ["boundary"], postgresql_using="gist")
    op.create_table(
        "coffee_varieties",
        sa.Column("variety_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False, unique=True),
        sa.Column("rust_resistance_level", sa.String(length=50), nullable=False),
        sa.Column("rust_susceptibility", sa.String(length=50), nullable=False),
        sa.Column("optimal_temp_min_c", sa.Float(), nullable=False),
        sa.Column("optimal_temp_max_c", sa.Float(), nullable=False),
    )
    op.create_table(
        "farm_plots",
        sa.Column("plot_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("variety_id", sa.Integer(), nullable=False),
        sa.Column(
            "growth_stage", sa.String(length=50), nullable=False, server_default="VEGETATIVE"
        ),
        sa.Column("plot_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("plot_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("cultivated_area_ha", sa.Float(), nullable=False, server_default="0"),
        sa.Column("plot_area_ha", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tree_density_per_ha", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("crop_age_years", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("polygon_geojson", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variety_id"], ["coffee_varieties.variety_id"]),
    )
    op.create_index("ix_farm_plots_farm_id", "farm_plots", ["farm_id"])
    op.create_index("ix_farm_plots_variety_id", "farm_plots", ["variety_id"])
    op.create_table(
        "crop_nutrient_references",
        sa.Column("reference_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("variety_id", sa.Integer(), nullable=False),
        sa.Column("crop_label", sa.String(length=100), nullable=False),
        sa.Column("growth_stage", sa.String(length=50), nullable=False),
        sa.Column("target_ph_min", sa.Float(), nullable=False),
        sa.Column("target_ph_max", sa.Float(), nullable=False),
        sa.Column("ph_min", sa.Float(), nullable=False),
        sa.Column("ph_max", sa.Float(), nullable=False),
        sa.Column("req_nitrogen_kg_ha", sa.Float(), nullable=False),
        sa.Column("n_min_mg_kg", sa.Float(), nullable=False),
        sa.Column("n_max_mg_kg", sa.Float(), nullable=False),
        sa.Column("req_phosphorus_kg_ha", sa.Float(), nullable=False),
        sa.Column("p_min_mg_kg", sa.Float(), nullable=False),
        sa.Column("p_max_mg_kg", sa.Float(), nullable=False),
        sa.Column("req_potassium_kg_ha", sa.Float(), nullable=False),
        sa.Column("k_min_mg_kg", sa.Float(), nullable=False),
        sa.Column("k_max_mg_kg", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "variety_id", "growth_stage", name="uq_crop_nutrient_references_variety_stage"
        ),
        sa.ForeignKeyConstraint(
            ["variety_id"], ["coffee_varieties.variety_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_crop_nutrient_references_variety_id",
        "crop_nutrient_references",
        ["variety_id"],
    )
    op.create_table(
        "forest_reserves",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column(
            "boundary",
            Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False),
            nullable=False,
        ),
    )
    op.create_index("ix_forest_reserves_code", "forest_reserves", ["code"], unique=True)
    op.create_index(
        "ix_forest_reserves_boundary",
        "forest_reserves",
        ["boundary"],
        postgresql_using="gist",
    )
    op.create_table(
        "export_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_code", sa.String(length=100), nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=True),
        sa.Column("buyer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="EN_PROCESO"),
        sa.Column("total_weight_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("cupping_score", sa.Numeric(4, 2), nullable=True),
        sa.Column("weighted_water_m3_ton", sa.Numeric(8, 2), nullable=True),
        sa.Column("avg_fertilizer_red_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_exportable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deforestation_free", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["buyer_id"], ["buyers.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.CheckConstraint("total_weight_kg > 0", name="ck_export_batches_weight_positive"),
        sa.CheckConstraint(
            "cupping_score IS NULL OR cupping_score BETWEEN 0 AND 100",
            name="ck_export_batches_cupping_score_range",
        ),
        sa.CheckConstraint(
            "weighted_water_m3_ton IS NULL OR weighted_water_m3_ton >= 0",
            name="ck_export_batches_water_nonnegative",
        ),
        sa.CheckConstraint(
            "avg_fertilizer_red_pct IS NULL OR avg_fertilizer_red_pct BETWEEN -100 AND 100",
            name="ck_export_batches_fertilizer_reduction_range",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'EN_PROCESO', 'CERTIFIED', "
            "'EXPORTABLE_CERTIFICADO', 'REJECTED', 'RECHAZADO')",
            name="ck_export_batches_status",
        ),
    )
    op.create_index("ix_export_batches_batch_code", "export_batches", ["batch_code"], unique=True)
    op.create_index("ix_export_batches_farm_id", "export_batches", ["farm_id"])
    op.create_index("ix_export_batches_buyer_id", "export_batches", ["buyer_id"])
    op.create_index("ix_export_batches_status", "export_batches", ["status"])
    op.create_table(
        "batch_farm_compositions",
        sa.Column("batch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("contributed_weight_kg", sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["export_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("batch_id", "farm_id"),
        sa.CheckConstraint(
            "contributed_weight_kg > 0",
            name="ck_batch_farm_compositions_weight_positive",
        ),
    )
    op.create_index("ix_batch_farm_compositions_farm_id", "batch_farm_compositions", ["farm_id"])
    op.execute(
        """
        CREATE FUNCTION prevent_certified_lot_composition_change()
        RETURNS TRIGGER AS $$
        DECLARE
            target_batch_id UUID;
            current_status VARCHAR(50);
        BEGIN
            IF TG_OP = 'DELETE' THEN
                target_batch_id := OLD.batch_id;
            ELSE
                target_batch_id := NEW.batch_id;
            END IF;

            SELECT status INTO current_status
            FROM export_batches
            WHERE id = target_batch_id
            FOR UPDATE;

            IF current_status IN ('CERTIFIED', 'EXPORTABLE_CERTIFICADO') THEN
                RAISE EXCEPTION 'Certified lot composition is immutable';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_certified_lot_composition_change
        BEFORE INSERT OR UPDATE OR DELETE ON batch_farm_compositions
        FOR EACH ROW EXECUTE FUNCTION prevent_certified_lot_composition_change()
        """
    )
    op.create_table(
        "farm_visits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_farm_visits_farm_id", "farm_visits", ["farm_id"])
    op.create_index("ix_farm_visits_due_at", "farm_visits", ["due_at"])
    op.create_table(
        "soil_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ph", sa.Float(), nullable=False),
        sa.Column("nitrogen_mg_kg", sa.Float(), nullable=False),
        sa.Column("phosphorus_mg_kg", sa.Float(), nullable=False),
        sa.Column("potassium_mg_kg", sa.Float(), nullable=False),
        sa.Column(
            "source_data",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "measured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plot_id"], ["farm_plots.plot_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_soil_analyses_farm_id", "soil_analyses", ["farm_id"])
    op.create_index("ix_soil_analyses_plot_id", "soil_analyses", ["plot_id"])
    op.create_index("ix_soil_analyses_measured_at", "soil_analyses", ["measured_at"])
    op.create_table(
        "fertilizer_recommendations",
        sa.Column("recommendation_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("analysis_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("plot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("farm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("n_evaluation", sa.String(length=20), nullable=False),
        sa.Column("p_evaluation", sa.String(length=20), nullable=False),
        sa.Column("k_evaluation", sa.String(length=20), nullable=False),
        sa.Column("ph_evaluation", sa.String(length=20), nullable=False),
        sa.Column("apply_fertilizer", sa.Boolean(), nullable=False),
        sa.Column("recommended_n_kg_ha", sa.Numeric(6, 2), nullable=False),
        sa.Column("recommended_p_kg_ha", sa.Numeric(6, 2), nullable=False),
        sa.Column("recommended_k_kg_ha", sa.Numeric(6, 2), nullable=False),
        sa.Column("estimated_saving_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["analysis_id"], ["soil_analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plot_id"], ["farm_plots.plot_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "n_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_n_evaluation",
        ),
        sa.CheckConstraint(
            "p_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_p_evaluation",
        ),
        sa.CheckConstraint(
            "k_evaluation IN ('DEFICIENT', 'OPTIMAL', 'EXCESS_WARNING')",
            name="ck_fertilizer_recommendations_k_evaluation",
        ),
        sa.CheckConstraint(
            "ph_evaluation IN ('ACIDIC', 'OPTIMAL', 'ALKALINE')",
            name="ck_fertilizer_recommendations_ph_evaluation",
        ),
        sa.CheckConstraint(
            "recommended_n_kg_ha >= 0 AND recommended_p_kg_ha >= 0 AND recommended_k_kg_ha >= 0",
            name="ck_fertilizer_recommendations_doses_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_saving_pct BETWEEN 0 AND 100",
            name="ck_fertilizer_recommendations_saving_range",
        ),
    )
    op.create_index(
        "ix_fertilizer_recommendations_analysis_id",
        "fertilizer_recommendations",
        ["analysis_id"],
    )
    op.create_index(
        "ix_fertilizer_recommendations_farm_id",
        "fertilizer_recommendations",
        ["farm_id"],
    )
    op.create_index(
        "ix_fertilizer_recommendations_farm_generated",
        "fertilizer_recommendations",
        ["farm_id", sa.text("generated_at DESC")],
    )
    op.create_index(
        "ix_fertilizer_recommendations_plot_id",
        "fertilizer_recommendations",
        ["plot_id"],
    )
    op.create_index(
        "ix_fertilizer_recommendations_plot_generated",
        "fertilizer_recommendations",
        ["plot_id", sa.text("generated_at DESC")],
    )
    op.create_table(
        "traceability_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["export_batches.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_traceability_events_batch_id", "traceability_events", ["batch_id"])
    op.create_index("ix_traceability_events_occurred_at", "traceability_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_traceability_events_occurred_at", table_name="traceability_events")
    op.drop_index("ix_traceability_events_batch_id", table_name="traceability_events")
    op.drop_table("traceability_events")
    op.drop_index(
        "ix_fertilizer_recommendations_plot_generated",
        table_name="fertilizer_recommendations",
    )
    op.drop_index(
        "ix_fertilizer_recommendations_plot_id",
        table_name="fertilizer_recommendations",
    )
    op.drop_index(
        "ix_fertilizer_recommendations_analysis_id",
        table_name="fertilizer_recommendations",
    )
    op.drop_index(
        "ix_fertilizer_recommendations_farm_generated",
        table_name="fertilizer_recommendations",
    )
    op.drop_index(
        "ix_fertilizer_recommendations_farm_id",
        table_name="fertilizer_recommendations",
    )
    op.drop_table("fertilizer_recommendations")
    op.drop_index("ix_batch_farm_compositions_farm_id", table_name="batch_farm_compositions")
    op.execute(
        "DROP TRIGGER trg_prevent_certified_lot_composition_change ON batch_farm_compositions"
    )
    op.execute("DROP FUNCTION prevent_certified_lot_composition_change()")
    op.drop_table("batch_farm_compositions")
    op.drop_index("ix_soil_analyses_measured_at", table_name="soil_analyses")
    op.drop_index("ix_soil_analyses_plot_id", table_name="soil_analyses")
    op.drop_index("ix_soil_analyses_farm_id", table_name="soil_analyses")
    op.drop_table("soil_analyses")
    op.drop_index("ix_farm_visits_due_at", table_name="farm_visits")
    op.drop_index("ix_farm_visits_farm_id", table_name="farm_visits")
    op.drop_table("farm_visits")
    op.drop_index("ix_export_batches_buyer_id", table_name="export_batches")
    op.drop_index("ix_export_batches_farm_id", table_name="export_batches")
    op.drop_index("ix_export_batches_batch_code", table_name="export_batches")
    op.drop_table("export_batches")
    op.drop_index("ix_forest_reserves_boundary", table_name="forest_reserves")
    op.drop_index("ix_forest_reserves_code", table_name="forest_reserves")
    op.drop_table("forest_reserves")
    op.drop_index("ix_crop_nutrient_references_variety_id", table_name="crop_nutrient_references")
    op.drop_table("crop_nutrient_references")
    op.drop_index("ix_farm_plots_variety_id", table_name="farm_plots")
    op.drop_index("ix_farm_plots_farm_id", table_name="farm_plots")
    op.drop_table("farm_plots")
    op.drop_table("coffee_varieties")
    op.drop_index("ix_farms_boundary", table_name="farms")
    op.drop_index("ix_farms_code", table_name="farms")
    op.drop_table("farms")
    op.drop_table("buyers")
