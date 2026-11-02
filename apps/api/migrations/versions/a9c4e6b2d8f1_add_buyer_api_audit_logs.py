"""Tabla buyer_api_audit_logs: auditoría de consultas de compradores (CA-09, RNF-05)

Implementa la tabla 8 de ``data_structure.md``. Cada consulta de
``GET /api/v1/traceability/{batch_code}`` que responde ``200`` o ``403`` deja una fila con el
comprador, el lote, el código HTTP, la latencia y la hora de la solicitud.

La API escribe con ``log_buyer_api_access(...)``, una función ``SECURITY DEFINER``:

- toma el comprador de ``app.current_buyer_id`` (la variable que fija la API tras validar el
  JWT), así que un comprador no puede registrar filas a nombre de otro;
- resuelve ``batch_id`` con el código del lote aunque el lote sea ajeno (invisible bajo RLS),
  fijando ``app.current_role = 'operator'`` con ``set_config`` solo durante la inserción y
  restaurando el valor anterior (mismo patrón que ``export_batch_exists``, migración
  ``f3b8c1d4a6e2``).

RLS forzado: el comprador solo lee sus propias filas y no puede escribir directamente; el
operador interno tiene acceso completo.

Revision ID: a9c4e6b2d8f1
Revises: f3b8c1d4a6e2
Create Date: 2026-11-02 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9c4e6b2d8f1"
down_revision: str | None = "f3b8c1d4a6e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "buyer_api_audit_logs"
APP_ROLE = "agrosense_app"
CURRENT_BUYER = "NULLIF(current_setting('app.current_buyer_id', true), '')::uuid"
IS_OPERATOR = "current_setting('app.current_role', true) = 'operator'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("http_status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(8, 2), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["buyer_id"],
            ["international_buyers.buyer_id"],
            name=op.f("fk_buyer_api_audit_logs_buyer_id_international_buyers"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["export_batches.batch_id"],
            name=op.f("fk_buyer_api_audit_logs_batch_id_export_batches"),
        ),
        sa.PrimaryKeyConstraint("audit_id", name=op.f("pk_buyer_api_audit_logs")),
    )
    op.create_index(op.f("ix_buyer_api_audit_logs_buyer_id"), TABLE, ["buyer_id"])
    op.create_index(op.f("ix_buyer_api_audit_logs_requested_at"), TABLE, ["requested_at"])

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY rls_{TABLE}_buyer_select ON {TABLE}
        FOR SELECT
        USING (buyer_id = {CURRENT_BUYER});
        """
    )
    op.execute(
        f"""
        CREATE POLICY rls_{TABLE}_operator_all ON {TABLE}
        FOR ALL
        USING ({IS_OPERATOR})
        WITH CHECK ({IS_OPERATOR});
        """
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION log_buyer_api_access(
            p_batch_code text,
            p_http_status_code integer,
            p_latency_ms numeric,
            p_requested_at timestamptz
        )
        RETURNS void
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            prev_role text := coalesce(current_setting('app.current_role', true), '');
            v_buyer_id uuid := {CURRENT_BUYER};
        BEGIN
            IF v_buyer_id IS NULL THEN
                RAISE EXCEPTION 'app.current_buyer_id is not set';
            END IF;
            PERFORM set_config('app.current_role', 'operator', true);
            INSERT INTO {TABLE} (buyer_id, batch_id, http_status_code, latency_ms, requested_at)
            VALUES (
                v_buyer_id,
                (SELECT batch_id FROM export_batches WHERE batch_code = p_batch_code),
                p_http_status_code,
                p_latency_ms,
                p_requested_at
            );
            PERFORM set_config('app.current_role', prev_role, true);
        END;
        $$;
        """
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                GRANT SELECT, INSERT ON {TABLE} TO {APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS log_buyer_api_access(text, integer, numeric, timestamptz);")
    op.drop_index(op.f("ix_buyer_api_audit_logs_requested_at"), table_name=TABLE)
    op.drop_index(op.f("ix_buyer_api_audit_logs_buyer_id"), table_name=TABLE)
    op.drop_table(TABLE)
