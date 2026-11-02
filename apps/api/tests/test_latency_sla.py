import asyncio
import math
import os
from time import perf_counter

import httpx
import pytest

BASE_URL = os.getenv("AGROSENSE_PERF_BASE_URL")
ACCESS_TOKEN = os.getenv("AGROSENSE_PERF_TOKEN")
LOT_ID = os.getenv("AGROSENSE_PERF_LOT_ID")
FARM_ID = os.getenv("AGROSENSE_PERF_FARM_ID")

pytestmark = pytest.mark.skipif(
    not all((BASE_URL, ACCESS_TOKEN, LOT_ID, FARM_ID)),
    reason=(
        "Set AGROSENSE_PERF_BASE_URL, AGROSENSE_PERF_TOKEN, "
        "AGROSENSE_PERF_LOT_ID, and AGROSENSE_PERF_FARM_ID to run the latency SLA test"
    ),
)


async def _measure_endpoint(client: httpx.AsyncClient, path: str) -> list[float]:
    semaphore = asyncio.Semaphore(50)

    async def request_once() -> float:
        async with semaphore:
            started_at = perf_counter()
            response = await client.get(path)
            elapsed_ms = (perf_counter() - started_at) * 1000
            assert response.status_code == 200, f"{path}: HTTP {response.status_code}"
            return elapsed_ms

    return await asyncio.gather(*(request_once() for _ in range(200)))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_lot_and_farm_reads_meet_p95_latency_sla() -> None:
    assert BASE_URL is not None
    assert ACCESS_TOKEN is not None
    assert LOT_ID is not None
    assert FARM_ID is not None

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        timeout=15,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
    ) as client:
        lot_latencies, farm_latencies = await asyncio.gather(
            _measure_endpoint(client, f"/api/v1/lots/{LOT_ID}"),
            _measure_endpoint(client, f"/api/v1/farms/{FARM_ID}"),
        )

    lot_p95 = sorted(lot_latencies)[math.ceil(0.95 * len(lot_latencies)) - 1]
    farm_p95 = sorted(farm_latencies)[math.ceil(0.95 * len(farm_latencies)) - 1]
    assert lot_p95 < 500, f"Lot traceability p95 was {lot_p95:.1f} ms"
    assert farm_p95 < 500, f"Farm breakdown p95 was {farm_p95:.1f} ms"
