"""Unit and integration tests for the seed runner and database seeder."""

import os

import pytest
from agrosense_api.models import Base
from sqlalchemy import create_engine, text

from scripts.seed.faker_generators import AgroSenseFakerGenerator
from scripts.seed.run_seed import SeedConfig, build_parser, parse_args
from scripts.seed.seeder import DatabaseSeeder, resolve_database_url

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg:///agrosense_test_db",
)


def test_build_parser_defaults():
    """Verifica que el parser CLI tenga valores por defecto coherentes."""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.mode in {"dev", "prod"}
    assert args.seed == 42
    assert not args.reset
    assert args.db_url is None
    assert not args.echo


def test_parse_args_explicit():
    """Verifica el parseo de argumentos CLI explícitos."""
    config = parse_args(["--mode", "prod", "--seed", "123", "--reset", "--echo"])
    assert isinstance(config, SeedConfig)
    assert config.mode == "prod"
    assert config.seed == 123
    assert config.reset
    assert config.echo


def test_resolve_database_url(monkeypatch):
    """Verifica la resolución jerárquica de URL de base de datos."""
    # 1. Explícito
    assert (
        resolve_database_url("postgresql+psycopg://user:pass@host/db")
        == "postgresql+psycopg://user:pass@host/db"
    )

    # 2. Conversión de asyncpg a psycopg
    assert (
        resolve_database_url("postgresql+asyncpg://user:pass@host/db")
        == "postgresql+psycopg://user:pass@host/db"
    )

    # 3. Variable DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://env_user:pass@host/db")
    assert resolve_database_url() == "postgresql+psycopg://env_user:pass@host/db"

    # 4. Variable AGROSENSE_DATABASE_URL con asyncpg
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("AGROSENSE_DATABASE_URL", "postgresql+asyncpg://agro_user:pass@host/db")
    assert resolve_database_url() == "postgresql+psycopg://agro_user:pass@host/db"


@pytest.fixture(scope="module")
def postgres_test_engine():
    """Inicializa motor PostgreSQL/PostGIS para prueba de inserción real."""
    try:
        engine = create_engine(TEST_DATABASE_URL, echo=False)
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))
    except Exception as exc:
        pytest.skip(f"Base de datos PostgreSQL/PostGIS no disponible: {exc}")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    yield engine

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_seeder_persists_in_postgresql(postgres_test_engine):
    """Verifica que DatabaseSeeder inserte de verdad en PostgreSQL y sea idempotente."""
    gen = AgroSenseFakerGenerator(seed=42)
    dataset = gen.generate_all(mode="dev")

    seeder = DatabaseSeeder(database_url=TEST_DATABASE_URL)

    # 1. Primera ejecución: poblar tablas vacías
    counts_run_1 = seeder.seed(dataset=dataset, reset=True)
    assert counts_run_1["farms"] == 50
    assert counts_run_1["export_batches"] == 12
    assert counts_run_1["producers"] == 25
    assert counts_run_1["municipalities"] > 0
    assert counts_run_1["coffee_varieties"] > 0

    # 2. Segunda ejecución sin reset: verificar idempotencia sin duplicar
    counts_run_2 = seeder.seed(dataset=dataset, reset=False)
    assert counts_run_2 == counts_run_1
