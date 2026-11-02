"""Tests de migracion y en vivo para SCRUM-157 / RN-11:
Aislamiento multi-tenant de compradores con PostgreSQL Row-Level Security.

Valida:
1. La migracion b7d4e2f9a1c3 reemplaza las politicas permisivas por politicas deny-by-default,
   fuerza RLS (FORCE ROW LEVEL SECURITY) y define una ruta explicita de operador.
2. En PostgreSQL real (base migrada con ``alembic upgrade head``), usando el rol de aplicacion
   ``agrosense_app`` (no dueno, sin BYPASSRLS): el Comprador A ve cero filas del Comprador B,
   sin variable de sesion no se ve nada y la ruta de operador ve todo.

Los tests en vivo usan TEST_MIGRATED_DATABASE_URL (o TEST_DATABASE_URL) y se omiten si la
base no esta disponible o no esta migrada.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "apps" / "api" / "migrations" / "versions"
MIGRATED_DB_URL = os.getenv("TEST_MIGRATED_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
APP_ROLE = "agrosense_app"


def test_rls_migration_enforces_deny_by_default_policies() -> None:
    """La migracion vigente fuerza RLS y no deja pasar filas cuando falta el comprador."""
    migration = MIGRATIONS_DIR / "b7d4e2f9a1c3_enforce_deny_by_default_buyer_isolation.py"
    content = migration.read_text(encoding="utf-8")

    assert "FORCE ROW LEVEL SECURITY" in content
    assert "DROP POLICY IF EXISTS rls_export_batches_buyer_isolation" in content
    assert "DROP POLICY IF EXISTS rls_buyer_batch_contracts_isolation" in content
    assert "CREATE POLICY rls_buyer_batch_contracts_buyer_select" in content
    assert "CREATE POLICY rls_export_batches_buyer_select" in content
    assert "current_setting('app.current_buyer_id', true)" in content
    assert "current_setting('app.current_role', true) = 'operator'" in content

    upgrade_body = content.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    # Las politicas nuevas no deben tener la rama permisiva "variable vacia => todo visible".
    assert "IS NULL" not in upgrade_body
    assert "= ''" not in upgrade_body


# ---------------------------------------------------------------------------
# Tests en vivo contra PostgreSQL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rls_engine() -> Iterator[Engine]:
    if not MIGRATED_DB_URL:
        pytest.skip("TEST_MIGRATED_DATABASE_URL/TEST_DATABASE_URL no configurada.")
    engine = create_engine(MIGRATED_DB_URL, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            ready = conn.execute(
                text(
                    "SELECT to_regclass('public.export_batches') IS NOT NULL AND EXISTS ("
                    " SELECT 1 FROM pg_policies"
                    " WHERE policyname = 'rls_export_batches_buyer_select')"
                )
            ).scalar()
    except Exception as exc:  # pragma: no cover - depende del entorno
        engine.dispose()
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    if not ready:
        engine.dispose()
        pytest.skip("La base de pruebas no esta migrada (ejecuta `alembic upgrade head`).")

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                        CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END
                $$;
                """
            )
        )
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {APP_ROLE}"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def two_buyers(rls_engine: Engine) -> Iterator[dict[str, UUID]]:
    ids = {
        "buyer_a": uuid4(),
        "buyer_b": uuid4(),
        "batch_a": uuid4(),
        "batch_b": uuid4(),
    }
    suffix = uuid4().hex[:8]
    with rls_engine.begin() as conn:
        # Ruta de operador: funciona aunque el usuario de pruebas no sea superusuario.
        conn.execute(text("SELECT set_config('app.current_role', 'operator', true)"))
        for key in ("a", "b"):
            conn.execute(
                text(
                    "INSERT INTO international_buyers (buyer_id, client_id, buyer_name) "
                    "VALUES (:id, :client, :name)"
                ),
                {"id": ids[f"buyer_{key}"], "client": f"RLS-{key}-{suffix}", "name": key},
            )
            conn.execute(
                text(
                    "INSERT INTO export_batches (batch_id, batch_code, total_weight_kg) "
                    "VALUES (:id, :code, 100)"
                ),
                {"id": ids[f"batch_{key}"], "code": f"RLS-LOT-{key}-{suffix}"},
            )
            conn.execute(
                text(
                    "INSERT INTO buyer_batch_contracts (contract_id, batch_id, buyer_id) "
                    "VALUES (:cid, :batch, :buyer)"
                ),
                {"cid": uuid4(), "batch": ids[f"batch_{key}"], "buyer": ids[f"buyer_{key}"]},
            )
    yield ids
    with rls_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.current_role', 'operator', true)"))
        batch_ids = [ids["batch_a"], ids["batch_b"]]
        buyer_ids = [ids["buyer_a"], ids["buyer_b"]]
        conn.execute(
            text("DELETE FROM buyer_batch_contracts WHERE batch_id = ANY(:ids)"),
            {"ids": batch_ids},
        )
        conn.execute(
            text("DELETE FROM export_batches WHERE batch_id = ANY(:ids)"), {"ids": batch_ids}
        )
        conn.execute(
            text("DELETE FROM international_buyers WHERE buyer_id = ANY(:ids)"),
            {"ids": buyer_ids},
        )


def _as_app_role(conn: Connection, *, buyer_id: UUID | None = None, operator: bool = False) -> None:
    conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    if buyer_id is not None:
        conn.execute(
            text("SELECT set_config('app.current_buyer_id', :bid, true)"), {"bid": str(buyer_id)}
        )
    if operator:
        conn.execute(text("SELECT set_config('app.current_role', 'operator', true)"))


def _visible_batches(conn: Connection, ids: dict[str, UUID]) -> set[UUID]:
    rows = conn.execute(
        text("SELECT batch_id FROM export_batches WHERE batch_id = ANY(:ids)"),
        {"ids": [ids["batch_a"], ids["batch_b"]]},
    )
    return {row[0] for row in rows}


def _visible_contract_buyers(conn: Connection, ids: dict[str, UUID]) -> set[UUID]:
    rows = conn.execute(
        text("SELECT buyer_id FROM buyer_batch_contracts WHERE batch_id = ANY(:ids)"),
        {"ids": [ids["batch_a"], ids["batch_b"]]},
    )
    return {row[0] for row in rows}


def test_app_role_is_not_owner_and_cannot_bypass_rls(rls_engine: Engine) -> None:
    with rls_engine.connect() as conn:
        bypass = conn.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": APP_ROLE},
        ).scalar()
        owners = set(
            conn.execute(
                text(
                    "SELECT tableowner FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename IN ('export_batches', 'buyer_batch_contracts')"
                )
            ).scalars()
        )
        forced = conn.execute(
            text(
                "SELECT bool_and(relforcerowsecurity) FROM pg_class "
                "WHERE relname IN ('export_batches', 'buyer_batch_contracts')"
            )
        ).scalar()
    assert bypass is False
    assert APP_ROLE not in owners
    assert forced is True


def test_buyer_a_sees_zero_rows_of_buyer_b(rls_engine: Engine, two_buyers) -> None:
    with rls_engine.begin() as conn:
        _as_app_role(conn, buyer_id=two_buyers["buyer_a"])
        assert _visible_batches(conn, two_buyers) == {two_buyers["batch_a"]}
        assert _visible_contract_buyers(conn, two_buyers) == {two_buyers["buyer_a"]}
        leaked = conn.execute(
            text("SELECT count(*) FROM export_batches WHERE batch_id = :b"),
            {"b": two_buyers["batch_b"]},
        ).scalar()
        assert leaked == 0


def test_without_buyer_context_no_rows_are_visible(rls_engine: Engine, two_buyers) -> None:
    with rls_engine.begin() as conn:
        _as_app_role(conn)
        assert _visible_batches(conn, two_buyers) == set()
        assert _visible_contract_buyers(conn, two_buyers) == set()


def test_operator_path_sees_all_rows(rls_engine: Engine, two_buyers) -> None:
    with rls_engine.begin() as conn:
        _as_app_role(conn, operator=True)
        assert _visible_batches(conn, two_buyers) == {
            two_buyers["batch_a"],
            two_buyers["batch_b"],
        }


def test_buyer_cannot_write_contracts(rls_engine: Engine, two_buyers) -> None:
    with pytest.raises(DBAPIError), rls_engine.begin() as conn:
        _as_app_role(conn, buyer_id=two_buyers["buyer_a"])
        conn.execute(
            text(
                "INSERT INTO buyer_batch_contracts (contract_id, batch_id, buyer_id) "
                "VALUES (:cid, :batch, :buyer)"
            ),
            {"cid": uuid4(), "batch": two_buyers["batch_b"], "buyer": two_buyers["buyer_a"]},
        )


@pytest.mark.anyio
async def test_api_service_uses_set_local_buyer_context(rls_engine: Engine, two_buyers) -> None:
    """La ruta real de la API (set_buyer_context + get_buyer_batches) respeta RLS."""
    from agrosense_api.db.session import set_buyer_context
    from agrosense_api.services.traceability import get_buyer_batches
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    async_engine = create_async_engine(MIGRATED_DB_URL)
    try:
        async with AsyncSession(async_engine) as session:
            await session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
            await set_buyer_context(session, two_buyers["buyer_a"])
            # Aun pidiendo los lotes del comprador B, RLS no devuelve nada.
            assert await get_buyer_batches(session, two_buyers["buyer_b"]) == []
            own = await get_buyer_batches(session, two_buyers["buyer_a"])
            assert [b.batch_id for b in own] == [two_buyers["batch_a"]]
    finally:
        await async_engine.dispose()
