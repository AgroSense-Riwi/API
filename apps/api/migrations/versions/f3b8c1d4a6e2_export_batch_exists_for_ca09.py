"""Función export_batch_exists para distinguir 403 de 404 bajo RLS (CA-09)

Con ``FORCE ROW LEVEL SECURITY`` y el rol ``agrosense_app``, un comprador no ve los lotes
de otros compradores: la consulta devuelve cero filas y la API respondía 404. CA-09 exige
un error de autorización (403) cuando el lote existe pero no es del comprador.

``export_batch_exists(code)`` es ``SECURITY DEFINER``: fija ``app.current_role = 'operator'``
con ``set_config`` solo mientras consulta y restaura el valor anterior antes de devolver, así
que ve la fila por la política de operador sin depender de que el dueño tenga BYPASSRLS. Solo
devuelve un booleano: no expone datos del lote.

No se usa la cláusula ``SET app.current_role`` de la función porque Postgres exige ser
superusuario para fijar un parámetro personalizado en la definición, y en EC2 el dueño de las
tablas no lo es ("permission denied to set parameter").

Revision ID: f3b8c1d4a6e2
Revises: e5a9b3c7d1f2
Create Date: 2026-11-02 21:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3b8c1d4a6e2"
down_revision: str | None = "e5a9b3c7d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION export_batch_exists(p_batch_code text)
        RETURNS boolean
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            prev_role text := coalesce(current_setting('app.current_role', true), '');
            found_batch boolean;
        BEGIN
            PERFORM set_config('app.current_role', 'operator', true);
            SELECT EXISTS (SELECT 1 FROM export_batches WHERE batch_code = p_batch_code)
              INTO found_batch;
            PERFORM set_config('app.current_role', prev_role, true);
            RETURN found_batch;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS export_batch_exists(text);")
