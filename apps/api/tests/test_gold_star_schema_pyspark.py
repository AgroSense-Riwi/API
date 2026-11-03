"""Tests del job PySpark de la capa Gold (SCRUM-184 / SCRUM-185, CT-07).

- Las consultas de extracción se ejecutan contra la base migrada con Alembic
  (TEST_MIGRATED_DATABASE_URL); se omiten si no está disponible.
- Las transformaciones usan una SparkSession local; se omiten si pyspark o Java no
  están instalados (en CI se instalan con el grupo ``spark``).
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pyarrow.parquet as pq
import pytest
from agrosense_api.services.star_schema_gold import ATHENA_GOLD_STAR_SCHEMA_DDL
from sqlalchemy import create_engine, text

from pipelines.spark_jobs.gold_star_schema_pyspark import (
    DELIVERIES_QUERY,
    FARMS_QUERY,
    GOLD_TABLES,
    SET_OPERATOR_ROLE,
    build_gold_tables,
    build_spark_session,
    to_libpq_dsn,
    upload_gold_tables,
    write_gold_tables,
)

MIGRATED_DB_URL = os.getenv("TEST_MIGRATED_DATABASE_URL")

FARMS = [
    {
        "farm_id": "11111111-1111-1111-1111-111111111111",
        "farm_code": "FCA-001",
        "farm_name": "Productor Uno",
        "municipality_name": "Chinchiná",
        "total_area_hectares": 4.5,
        "latitude": 4.98,
        "longitude": -75.60,
    },
    {
        "farm_id": "22222222-2222-2222-2222-222222222222",
        "farm_code": "FCA-002",
        "farm_name": "Productor Dos",
        "municipality_name": "Manizales",
        "total_area_hectares": 2.0,
        "latitude": 5.06,
        "longitude": -75.51,
    },
]

DELIVERIES = [
    {
        "delivery_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "delivery_date": date(2026, 10, 10),
        "farm_id": FARMS[0]["farm_id"],
        "variety_id": 1,
        "delivered_weight_kg": 1250.5,
        "batch_id": "bbbbbbbb-0000-0000-0000-000000000001",
        "batch_code": "EXP-2026-0001",
    },
    {
        "delivery_id": "aaaaaaaa-0000-0000-0000-000000000002",
        "delivery_date": date(2026, 11, 1),
        "farm_id": FARMS[1]["farm_id"],
        "variety_id": None,
        "delivered_weight_kg": 300.0,
        "batch_id": None,
        "batch_code": None,
    },
]

ATHENA_TO_SPARK = {"STRING": "string", "INT": "int", "DOUBLE": "double", "BOOLEAN": "boolean"}


def _athena_columns(table: str) -> list[tuple[str, str]]:
    block = re.search(
        rf"agrosense_datalake\.{table} \((.*?)\)\s*STORED AS PARQUET",
        ATHENA_GOLD_STAR_SCHEMA_DDL,
        re.S,
    )
    assert block, table
    cols = []
    for line in block.group(1).strip().splitlines():
        name, athena_type = line.strip().rstrip(",").split()
        cols.append((name, ATHENA_TO_SPARK[athena_type]))
    return cols


def test_to_libpq_dsn_strips_sqlalchemy_driver() -> None:
    assert to_libpq_dsn("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert to_libpq_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"
    assert to_libpq_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


# ---------------------------------------------------------------------------
# Consultas de extracción contra la base migrada
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Any]:
    if not MIGRATED_DB_URL:
        pytest.skip("TEST_MIGRATED_DATABASE_URL no configurada.")
    engine = create_engine(MIGRATED_DB_URL, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            ready = conn.execute(
                text("SELECT to_regclass('public.batch_farm_compositions') IS NOT NULL")
            ).scalar()
    except Exception as exc:  # pragma: no cover - depende del entorno
        engine.dispose()
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    if not ready:
        engine.dispose()
        pytest.skip("La base de pruebas no esta migrada (ejecuta `alembic upgrade head`).")
    yield engine
    engine.dispose()


@pytest.mark.parametrize(
    ("query", "columns"),
    [
        (
            FARMS_QUERY,
            [
                "farm_id",
                "farm_code",
                "farm_name",
                "municipality_name",
                "total_area_hectares",
                "latitude",
                "longitude",
            ],
        ),
        (
            DELIVERIES_QUERY,
            [
                "delivery_id",
                "delivery_date",
                "farm_id",
                "variety_id",
                "delivered_weight_kg",
                "batch_id",
                "batch_code",
            ],
        ),
    ],
)
def test_extraction_queries_match_migrated_schema(
    migrated_engine: Any, query: str, columns: list[str]
) -> None:
    with migrated_engine.connect() as conn:
        conn.execute(text(SET_OPERATOR_ROLE))
        result = conn.execute(text(query))
        assert list(result.keys()) == columns


# ---------------------------------------------------------------------------
# Transformaciones con SparkSession local
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    pytest.importorskip("pyspark")
    if not (shutil.which("java") or os.getenv("JAVA_HOME")):
        pytest.skip("Java no está instalado (requerido por PySpark).")
    os.environ.setdefault("SPARK_MASTER", "local[1]")
    session = build_spark_session("agrosense-gold-tests")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def gold_tables(spark: Any) -> dict[str, Any]:
    return build_gold_tables(spark, FARMS, DELIVERIES)


@pytest.mark.parametrize("table", GOLD_TABLES)
def test_gold_schema_matches_athena_ddl(gold_tables: dict[str, Any], table: str) -> None:
    spark_cols = [(f.name, f.dataType.simpleString()) for f in gold_tables[table].schema.fields]
    assert spark_cols == _athena_columns(table)


def test_dim_finca_rows(gold_tables: dict[str, Any]) -> None:
    rows = {r["farm_code"]: r for r in gold_tables["dim_finca"].collect()}
    assert set(rows) == {"FCA-001", "FCA-002"}
    assert rows["FCA-001"]["municipality_name"] == "Chinchiná"
    assert rows["FCA-001"]["altitude_meters"] is None


def test_dim_tiempo_covers_whole_year_with_iso_weekdays(gold_tables: dict[str, Any]) -> None:
    rows = gold_tables["dim_tiempo"].orderBy("date_key").collect()
    assert len(rows) == 365
    assert rows[0]["date_key"] == 20260101
    assert rows[-1]["date_key"] == 20261231
    for row in rows[:14]:
        d = date.fromisoformat(row["full_date"])
        assert row["day_of_week"] == d.isoweekday()
        assert row["is_weekend"] == (d.isoweekday() >= 6)
        assert row["quarter"] == 1


def test_fact_entregas_rows(gold_tables: dict[str, Any]) -> None:
    rows = {r["delivery_id"]: r for r in gold_tables["fact_entregas"].collect()}
    first = rows["aaaaaaaa-0000-0000-0000-000000000001"]
    assert first["date_key"] == 20261010
    assert first["batch_code"] == "EXP-2026-0001"
    assert first["delivered_weight_kg"] == pytest.approx(1250.5)
    second = rows["aaaaaaaa-0000-0000-0000-000000000002"]
    assert second["batch_id"] is None
    assert second["variety_id"] is None
    assert gold_tables["fact_alertas"].count() == 0


def test_write_and_upload_replaces_s3_prefix(gold_tables: dict[str, Any], tmp_path: Path) -> None:
    paths = write_gold_tables(gold_tables, tmp_path)

    parts = sorted((tmp_path / "fact_entregas").glob("*.parquet"))
    assert parts
    meta = pq.ParquetFile(parts[0]).metadata
    assert meta.row_group(0).column(0).compression == "SNAPPY"
    assert pq.read_table(parts[0]).num_rows == 2

    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "gold/fact_entregas/data.snappy.parquet"}]}
    ]
    uploaded = upload_gold_tables(s3, "datalakeagrosense", paths)

    assert set(uploaded) == set(GOLD_TABLES)
    uris = [uri for v in uploaded.values() for uri in v]
    assert all(uri.startswith("s3://datalakeagrosense/gold/") for uri in uris)
    keys = [c.args[2] for c in s3.upload_file.call_args_list]
    assert all(k.endswith(".parquet") and "/_" not in k for k in keys)
    s3.delete_objects.assert_any_call(
        Bucket="datalakeagrosense",
        Delete={"Objects": [{"Key": "gold/fact_entregas/data.snappy.parquet"}]},
    )
