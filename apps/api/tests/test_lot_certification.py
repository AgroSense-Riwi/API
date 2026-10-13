from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from agrosense_api.models import ExportBatch
from agrosense_api.services.lots import LotCertificationError, certify_lot

LOT_ID = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ContributionRow(NamedTuple):
    contributed_weight_kg: Decimal
    farm_id: UUID
    code: str
    name: str
    boundary: object | None
    water_usage_m3_ton: Decimal | None
    fertilizer_saving_pct: Decimal


class FakeCertificationSession:
    def __init__(
        self,
        *,
        traced_weight: Decimal = Decimal("100"),
        rows: list[ContributionRow] | None = None,
        overlaps: str | None = None,
        reserve_catalog_exists: bool = True,
        lot: ExportBatch | None = None,
    ) -> None:
        self.batch = lot or ExportBatch(
            id=LOT_ID,
            batch_code="LOT-2026-001",
            status="EN_PROCESO",
            total_weight_kg=Decimal("100"),
            cupping_score=Decimal("84"),
            created_at=datetime(2026, 10, 13, tzinfo=UTC),
            is_exportable=False,
            is_deforestation_free=False,
        )
        self.traced_weight = traced_weight
        self.rows = rows or [
            ContributionRow(
                Decimal("75"),
                UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9"),
                "FIN-0001",
                "Farm One",
                object(),
                Decimal("120"),
                Decimal("20"),
            ),
            ContributionRow(
                Decimal("25"),
                UUID("1f62fd9c-d76a-4a4d-a869-62ae928a2c3c"),
                "FIN-0002",
                "Farm Two",
                object(),
                Decimal("160"),
                Decimal("0"),
            ),
        ]
        self.overlaps = overlaps
        self.reserve_catalog_exists = reserve_catalog_exists
        self.scalar_calls = 0
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def begin(self):
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    async def scalar(self, statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return self.batch
        if self.scalar_calls == 2:
            return self.traced_weight
        if self.scalar_calls == 3:
            return True
        if self.scalar_calls == 4:
            return self.reserve_catalog_exists
        if self.scalar_calls == 5:
            return self.overlaps
        if self.scalar_calls == 6:
            return self.batch
        raise AssertionError("Unexpected database scalar query")

    async def execute(self, statement):
        class Result:
            def all(self):
                return self_rows

        self_rows = self.rows
        return Result()

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        return None


@pytest.mark.anyio
async def test_certification_calculates_weighted_metrics_and_commits() -> None:
    session = FakeCertificationSession()

    certificate = await certify_lot(session, LOT_ID)

    assert session.committed
    assert not session.rolled_back
    assert certificate.status == "EXPORTABLE_CERTIFICADO"
    assert certificate.weighted_water_m3_ton == Decimal("130")
    assert certificate.avg_fertilizer_red_pct == Decimal("15")
    assert certificate.is_exportable
    assert certificate.is_deforestation_free
    assert len(session.added) == 1


@pytest.mark.anyio
async def test_certification_rolls_back_when_origin_weight_is_incomplete() -> None:
    session = FakeCertificationSession(traced_weight=Decimal("99"))

    with pytest.raises(LotCertificationError, match="RN-08"):
        await certify_lot(session, LOT_ID)

    assert session.rolled_back
    assert not session.committed
    assert session.batch.status == "EN_PROCESO"
    assert session.added == []


@pytest.mark.anyio
async def test_certification_rolls_back_for_reserve_overlap() -> None:
    session = FakeCertificationSession(overlaps="FIN-0002")

    with pytest.raises(LotCertificationError, match="RN-09"):
        await certify_lot(session, LOT_ID)

    assert session.rolled_back
    assert session.batch.status == "EN_PROCESO"
