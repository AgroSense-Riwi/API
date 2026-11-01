"""Reservas forestales y geometría de fincas para la validación espacial RN-09

* Nueva tabla ``forest_reserves`` (polígono EPSG:4326 con índice GIST).
* Nueva columna nullable ``farms.geom`` (perímetro EPSG:4326 con índice GIST). Es nullable
  para no romper el seed; la certificación trata una finca sin geometría como NO conforme.
* Trigger ``trg_check_farm_forest_overlap`` (traceability-schema-definition §3): recalcula
  ``farms.overlaps_reserve`` con ``ST_Intersects`` cuando la finca tiene geometría.

Revision ID: e5a9b3c7d1f2
Revises: b7d4e2f9a1c3
Create Date: 2026-10-31 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5a9b3c7d1f2"
down_revision: str | None = "b7d4e2f9a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "agrosense_app"


def upgrade() -> None:
    op.create_table(
        "forest_reserves",
        sa.Column(
            "reserve_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("municipality_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=150), nullable=False),
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
            ["municipality_id"],
            ["municipalities.municipality_id"],
            name="fk_forest_reserves_municipality_id",
        ),
        sa.PrimaryKeyConstraint("reserve_id", name=op.f("pk_forest_reserves")),
    )
    op.execute("ALTER TABLE forest_reserves ADD COLUMN geom geometry(POLYGON, 4326) NOT NULL;")
    op.execute("CREATE INDEX idx_forest_reserves_geom ON forest_reserves USING GIST (geom);")

    op.execute("ALTER TABLE farms ADD COLUMN geom geometry(POLYGON, 4326);")
    op.execute("CREATE INDEX idx_farms_geom ON farms USING GIST (geom);")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_farm_forest_overlap()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Sin geometría no hay evidencia espacial: se conserva el valor recibido y la
            -- certificación la tratará como no evaluada (no conforme).
            IF NEW.geom IS NOT NULL THEN
                NEW.overlaps_reserve := EXISTS (
                    SELECT 1 FROM forest_reserves rf WHERE ST_Intersects(NEW.geom, rf.geom)
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_check_farm_forest_overlap
        BEFORE INSERT OR UPDATE OF geom, overlaps_reserve ON farms
        FOR EACH ROW EXECUTE FUNCTION update_farm_forest_overlap();
        """
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON forest_reserves TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_check_farm_forest_overlap ON farms;")
    op.execute("DROP FUNCTION IF EXISTS update_farm_forest_overlap();")
    op.execute("DROP INDEX IF EXISTS idx_farms_geom;")
    op.execute("ALTER TABLE farms DROP COLUMN IF EXISTS geom;")
    op.execute("DROP INDEX IF EXISTS idx_forest_reserves_geom;")
    op.drop_table("forest_reserves")
