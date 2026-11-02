"""RN-09 contra PostGIS real: ST_Intersects entre farms.geom y forest_reserves.geom.

Se omite si TEST_MIGRATED_DATABASE_URL (o TEST_DATABASE_URL) no apunta a una base migrada
con ``alembic upgrade head``.
"""

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

MIGRATED_DB_URL = os.getenv("TEST_MIGRATED_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")

RESERVE_WKT = "POLYGON((-75.60 4.80, -75.50 4.80, -75.50 4.90, -75.60 4.90, -75.60 4.80))"
FARM_INSIDE_WKT = "POLYGON((-75.56 4.84, -75.54 4.84, -75.54 4.86, -75.56 4.86, -75.56 4.84))"
FARM_OUTSIDE_WKT = "POLYGON((-75.30 4.60, -75.29 4.60, -75.29 4.61, -75.30 4.61, -75.30 4.60))"


@pytest.fixture(scope="module")
def spatial_engine() -> Iterator[Engine]:
    if not MIGRATED_DB_URL:
        pytest.skip("TEST_MIGRATED_DATABASE_URL/TEST_DATABASE_URL no configurada.")
    engine = create_engine(MIGRATED_DB_URL, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            ready = conn.execute(
                text(
                    "SELECT to_regclass('public.forest_reserves') IS NOT NULL AND EXISTS ("
                    " SELECT 1 FROM pg_trigger WHERE tgname = 'trg_check_farm_forest_overlap')"
                )
            ).scalar()
    except Exception as exc:  # pragma: no cover - depende del entorno
        engine.dispose()
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    if not ready:
        engine.dispose()
        pytest.skip("La base de pruebas no esta migrada (ejecuta `alembic upgrade head`).")
    yield engine
    engine.dispose()


@pytest.fixture
def spatial_fixture(spatial_engine: Engine) -> Iterator[dict[str, UUID]]:
    ids = {
        "producer": uuid4(),
        "reserve": uuid4(),
        "farm_inside": uuid4(),
        "farm_outside": uuid4(),
        "farm_no_geom": uuid4(),
    }
    suffix = uuid4().hex[:8]
    with spatial_engine.begin() as conn:
        municipality_id = conn.execute(
            text("INSERT INTO municipalities (name) VALUES (:n) RETURNING municipality_id"),
            {"n": f"RN09-{suffix}"},
        ).scalar_one()
        ids["municipality"] = municipality_id
        conn.execute(
            text(
                "INSERT INTO producers (producer_id, document_number, full_name, "
                "cooperative_join_date) VALUES (:id, :doc, 'RN09 Test', CURRENT_DATE)"
            ),
            {"id": ids["producer"], "doc": f"RN09-{suffix}"},
        )
        conn.execute(
            text(
                "INSERT INTO forest_reserves (reserve_id, municipality_id, name, geom) "
                "VALUES (:id, :mun, 'PNN Prueba RN09', ST_GeomFromText(:wkt, 4326))"
            ),
            {"id": ids["reserve"], "mun": municipality_id, "wkt": RESERVE_WKT},
        )
        for key, wkt in (
            ("farm_inside", FARM_INSIDE_WKT),
            ("farm_outside", FARM_OUTSIDE_WKT),
            ("farm_no_geom", None),
        ):
            conn.execute(
                text(
                    "INSERT INTO farms (farm_id, farm_code, producer_id, municipality_id, "
                    "latitude, longitude, cultivated_area_ha, overlaps_reserve, geom) VALUES "
                    "(:id, :code, :producer, :mun, 4.85, -75.55, 2.0, false, "
                    "CASE WHEN CAST(:wkt AS text) IS NULL THEN NULL "
                    "ELSE ST_GeomFromText(CAST(:wkt AS text), 4326) END)"
                ),
                {
                    "id": ids[key],
                    "code": f"RN09-{key}-{suffix}",
                    "producer": ids["producer"],
                    "mun": municipality_id,
                    "wkt": wkt,
                },
            )
    yield ids
    with spatial_engine.begin() as conn:
        farm_ids = [ids["farm_inside"], ids["farm_outside"], ids["farm_no_geom"]]
        conn.execute(text("DELETE FROM farms WHERE farm_id = ANY(:ids)"), {"ids": farm_ids})
        conn.execute(
            text("DELETE FROM forest_reserves WHERE reserve_id = :id"), {"id": ids["reserve"]}
        )
        conn.execute(text("DELETE FROM producers WHERE producer_id = :id"), {"id": ids["producer"]})
        conn.execute(
            text("DELETE FROM municipalities WHERE municipality_id = :id"),
            {"id": ids["municipality"]},
        )


def test_trigger_flags_farm_overlapping_reserve(spatial_engine: Engine, spatial_fixture) -> None:
    with spatial_engine.connect() as conn:
        flags = dict(
            conn.execute(
                text("SELECT farm_id, overlaps_reserve FROM farms WHERE farm_id = ANY(:ids)"),
                {"ids": [spatial_fixture["farm_inside"], spatial_fixture["farm_outside"]]},
            ).all()
        )
    assert flags[spatial_fixture["farm_inside"]] is True
    assert flags[spatial_fixture["farm_outside"]] is False


@pytest.mark.anyio
async def test_certification_uses_postgis_intersection(spatial_fixture) -> None:
    from agrosense_api.services.certification_engine import (
        BatchCertificationStateMachine,
        BatchCertificationStatus,
        fetch_farm_spatial_compliance,
    )
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    async_engine = create_async_engine(MIGRATED_DB_URL)
    try:
        async with AsyncSession(async_engine) as session:
            rows = await fetch_farm_spatial_compliance(
                session,
                [
                    spatial_fixture["farm_inside"],
                    spatial_fixture["farm_outside"],
                    spatial_fixture["farm_no_geom"],
                ],
            )
    finally:
        await async_engine.dispose()

    by_farm = {row["farm_id"]: row for row in rows}
    inside = by_farm[spatial_fixture["farm_inside"]]
    assert inside["has_overlap"] is True
    assert inside["overlap_area_hectares"] > Decimal("0")
    assert inside["reserve_name"] == "PNN Prueba RN09"
    assert by_farm[spatial_fixture["farm_outside"]]["has_overlap"] is False
    assert by_farm[spatial_fixture["farm_no_geom"]]["geometry_evaluated"] is False

    clean = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("100.00"),
        compositions=[{"farm_id": spatial_fixture["farm_outside"], "contributed_kg": 100}],
        farm_overlaps=[by_farm[spatial_fixture["farm_outside"]]],
    )
    assert clean.current_status == BatchCertificationStatus.CERTIFICADO

    unknown = BatchCertificationStateMachine.evaluate_batch(
        batch_id=uuid4(),
        total_weight_kg=Decimal("100.00"),
        compositions=[{"farm_id": spatial_fixture["farm_no_geom"], "contributed_kg": 100}],
        farm_overlaps=[by_farm[spatial_fixture["farm_no_geom"]]],
    )
    assert unknown.current_status == BatchCertificationStatus.RECHAZADO
