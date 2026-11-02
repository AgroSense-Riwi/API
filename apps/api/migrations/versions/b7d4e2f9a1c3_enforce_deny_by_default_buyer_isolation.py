"""Aislamiento de compradores deny-by-default con FORCE ROW LEVEL SECURITY (RN-11)

Reemplaza las políticas de c1a8f93e2b45, que exponían todas las filas cuando
``app.current_buyer_id`` estaba vacío, por:

* Política de comprador (sólo SELECT): visible únicamente si la fila pertenece al comprador
  fijado con ``SET LOCAL app.current_buyer_id`` (``set_config(..., true)``). Sin variable
  -> cero filas.
* Política de operador (ALL): ruta explícita para código interno que ya autenticó a un
  operador (JWT + rol) y fija ``SET LOCAL app.current_role = 'operator'``.
* ``FORCE ROW LEVEL SECURITY``: las políticas aplican también al dueño de las tablas si no es
  superusuario ni tiene BYPASSRLS. Las migraciones y el seed se ejecutan con el rol dueño
  (superusuario en Docker / BYPASSRLS en EC2); la API usa el rol ``agrosense_app`` (no dueño).

Si el rol ``agrosense_app`` existe, se le otorgan privilegios DML sobre las tablas actuales.

Revision ID: b7d4e2f9a1c3
Revises: c8e2a7d5f0b1
Create Date: 2026-10-31 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d4e2f9a1c3"
down_revision: str | None = "c8e2a7d5f0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "agrosense_app"
CURRENT_BUYER = "NULLIF(current_setting('app.current_buyer_id', true), '')::uuid"
IS_OPERATOR = "current_setting('app.current_role', true) = 'operator'"

GRANT_APP_ROLE_SQL = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT USAGE ON SCHEMA public TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
    END IF;
END
$$;
"""


def upgrade() -> None:
    # 1. Retirar las políticas permisivas anteriores.
    op.execute("DROP POLICY IF EXISTS rls_export_batches_buyer_isolation ON export_batches;")
    op.execute(
        "DROP POLICY IF EXISTS rls_buyer_batch_contracts_isolation ON buyer_batch_contracts;"
    )

    # 2. RLS habilitado y forzado (también para el dueño sin BYPASSRLS).
    for table in ("buyer_batch_contracts", "export_batches"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

    # 3. Políticas de comprador: deny-by-default (sin variable de sesión -> cero filas).
    op.execute(
        f"""
        CREATE POLICY rls_buyer_batch_contracts_buyer_select ON buyer_batch_contracts
        FOR SELECT
        USING (buyer_id = {CURRENT_BUYER});
        """
    )
    op.execute(
        f"""
        CREATE POLICY rls_export_batches_buyer_select ON export_batches
        FOR SELECT
        USING (
            EXISTS (
                SELECT 1
                FROM buyer_batch_contracts c
                WHERE c.batch_id = export_batches.batch_id
                  AND c.buyer_id = {CURRENT_BUYER}
            )
        );
        """
    )

    # 4. Ruta explícita de operador interno (fijada sólo tras autenticar JWT + rol).
    for table in ("buyer_batch_contracts", "export_batches"):
        op.execute(
            f"""
            CREATE POLICY rls_{table}_operator_all ON {table}
            FOR ALL
            USING ({IS_OPERATOR})
            WITH CHECK ({IS_OPERATOR});
            """
        )

    # 5. Privilegios para el rol de aplicación (si ya fue creado por init/DBA).
    op.execute(GRANT_APP_ROLE_SQL)


def downgrade() -> None:
    for table in ("buyer_batch_contracts", "export_batches"):
        op.execute(f"DROP POLICY IF EXISTS rls_{table}_operator_all ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS rls_export_batches_buyer_select ON export_batches;")
    op.execute(
        "DROP POLICY IF EXISTS rls_buyer_batch_contracts_buyer_select ON buyer_batch_contracts;"
    )

    # Restaurar las políticas de c1a8f93e2b45.
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
