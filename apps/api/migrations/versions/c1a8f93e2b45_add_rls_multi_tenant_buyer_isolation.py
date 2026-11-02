"""Implementacion de Row-Level Security (RLS) para aislamiento multi-tenant de compradores

Revision ID: c1a8f93e2b45
Revises: 927ccb53d091
Create Date: 2026-10-26 14:10:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1a8f93e2b45"
down_revision: str | None = "927ccb53d091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Habilitar RLS en buyer_batch_contracts y export_batches
    op.execute("ALTER TABLE buyer_batch_contracts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE export_batches ENABLE ROW LEVEL SECURITY;")

    # 2. Politica RLS para buyer_batch_contracts:
    # Si app.current_buyer_id esta seteado, solo expone contratos de ese comprador.
    # Si no esta seteado o esta vacio, no filtra (permite acceso a procesos internos / admin).
    op.execute(
        """
        CREATE POLICY rls_buyer_batch_contracts_isolation ON buyer_batch_contracts
        FOR ALL
        USING (
            current_setting('app.current_buyer_id', true) IS NULL
            OR current_setting('app.current_buyer_id', true) = ''
            OR buyer_id = NULLIF(current_setting('app.current_buyer_id', true), '')::uuid
        );
        """
    )

    # 3. Politica RLS para export_batches:
    # Si app.current_buyer_id esta seteado, solo expone lotes asociados en buyer_batch_contracts.
    # Si no esta seteado o esta vacio, no filtra.
    op.execute(
        """
        CREATE POLICY rls_export_batches_buyer_isolation ON export_batches
        FOR ALL
        USING (
            current_setting('app.current_buyer_id', true) IS NULL
            OR current_setting('app.current_buyer_id', true) = ''
            OR batch_id IN (
                SELECT c.batch_id
                FROM buyer_batch_contracts c
                WHERE c.buyer_id = NULLIF(current_setting('app.current_buyer_id', true), '')::uuid
            )
        );
        """
    )


def downgrade() -> None:
    # 1. Eliminar politicas RLS
    op.execute("DROP POLICY IF EXISTS rls_export_batches_buyer_isolation ON export_batches;")
    op.execute(
        "DROP POLICY IF EXISTS rls_buyer_batch_contracts_isolation ON buyer_batch_contracts;"
    )

    # 2. Deshabilitar RLS en las tablas
    op.execute("ALTER TABLE export_batches DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE buyer_batch_contracts DISABLE ROW LEVEL SECURITY;")
