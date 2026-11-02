import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from agrosense_api.core.config import get_settings
from agrosense_api.core.security import (
    create_buyer_access_token,
    decode_buyer_access_token,
    hash_password,
)
from agrosense_api.models import InternationalBuyer
from agrosense_api.schemas.traceability import (
    BatchSummaryResponse,
    TraceabilityResponse,
)
from agrosense_api.script import app
from agrosense_api.services.traceability import (
    TraceabilityForbiddenError,
    TraceabilityNotFoundError,
    get_batch_trace,
)
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def setup_buyer_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AGROSENSE_BUYER_JWT_SECRET_KEY",
        "test_buyer_secret_key_at_least_32_chars_long",  # noqa: S105
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def buyer_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def buyer_token(buyer_id: uuid.UUID) -> str:
    return create_buyer_access_token(buyer_id)


BUYER_API_KEY = "not-a-real-key"  # noqa: S105


def _buyer(buyer_id: uuid.UUID, *, is_active: bool = True, with_hash: bool = True):
    return InternationalBuyer(
        buyer_id=buyer_id,
        client_id="BUYER-NORDIC-01",
        buyer_name="Nordic Roasters",
        api_key_hash=hash_password(BUYER_API_KEY) if with_hash else None,
        is_active=is_active,
    )


def test_issue_buyer_token_with_valid_client_credentials(
    client: TestClient, buyer_id: uuid.UUID, override_db
) -> None:
    override_db(_buyer(buyer_id))
    response = client.post(
        "/api/v1/auth/buyer-token",
        json={"client_id": "BUYER-NORDIC-01", "api_key": BUYER_API_KEY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"  # noqa: S105
    claims = decode_buyer_access_token(data["access_token"])
    assert claims["buyer_id"] == str(buyer_id)


@pytest.mark.parametrize(
    ("buyer_factory", "api_key"),
    [
        (lambda bid: _buyer(bid), "wrong-key"),
        (lambda bid: None, BUYER_API_KEY),
        (lambda bid: _buyer(bid, is_active=False), BUYER_API_KEY),
        (lambda bid: _buyer(bid, with_hash=False), BUYER_API_KEY),
    ],
    ids=["wrong-key", "unknown-client", "inactive-buyer", "no-key-provisioned"],
)
def test_issue_buyer_token_rejects_invalid_credentials(
    client: TestClient, buyer_id: uuid.UUID, override_db, buyer_factory, api_key: str
) -> None:
    override_db(buyer_factory(buyer_id))
    response = client.post(
        "/api/v1/auth/buyer-token",
        json={"client_id": "BUYER-NORDIC-01", "api_key": api_key},
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid client credentials"


def test_issue_buyer_token_no_longer_accepts_bare_buyer_id(
    client: TestClient, buyer_id: uuid.UUID, override_db
) -> None:
    override_db(_buyer(buyer_id))
    response = client.post("/api/v1/auth/buyer-token", json={"buyer_id": str(buyer_id)})
    assert response.status_code == 422


def test_traceability_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/traceability/batches")
    assert response.status_code in (401, 403)


def test_list_buyer_batches_success(
    client: TestClient, buyer_id: uuid.UUID, buyer_token: str
) -> None:
    fake_batches = [
        BatchSummaryResponse(
            batch_id=uuid.uuid4(),
            batch_code="LOTE-EXP-001",
            total_weight_kg=Decimal("1500.00"),
            traced_weight_kg=Decimal("1500.00"),
            is_exportable=True,
            is_certified=True,
            weighted_water_m3_ton=Decimal("3.45"),
            created_at=datetime.now(UTC),
        )
    ]
    with patch(
        "agrosense_api.api.v1.endpoints.traceability.get_buyer_batches",
        new=AsyncMock(return_value=fake_batches),
    ):
        response = client.get(
            "/api/v1/traceability/batches",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["batch_code"] == "LOTE-EXP-001"


def test_get_batch_trace_success(client: TestClient, buyer_id: uuid.UUID, buyer_token: str) -> None:
    fake_trace = TraceabilityResponse(
        batch_id=uuid.uuid4(),
        batch_code="LOTE-EXP-001",
        total_weight_kg=Decimal("1500.00"),
        traced_weight_kg=Decimal("1500.00"),
        is_exportable=True,
        is_certified=True,
        weighted_water_m3_ton=Decimal("3.45"),
        buyer_id=buyer_id,
        buyer_name="Blue Bottle Coffee",
        created_at=datetime.now(UTC),
        compositions=[],
        cupping_evaluations=[],
    )
    with patch(
        "agrosense_api.api.v1.endpoints.traceability.get_batch_trace",
        new=AsyncMock(return_value=fake_trace),
    ):
        response = client.get(
            "/api/v1/traceability/LOTE-EXP-001",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["batch_code"] == "LOTE-EXP-001"
        assert data["buyer_name"] == "Blue Bottle Coffee"


def test_get_batch_trace_forbidden(
    client: TestClient, buyer_id: uuid.UUID, buyer_token: str
) -> None:
    with patch(
        "agrosense_api.api.v1.endpoints.traceability.get_batch_trace",
        side_effect=TraceabilityForbiddenError("Este lote no está asignado a este comprador"),
    ):
        response = client.get(
            "/api/v1/traceability/LOTE-OTRO-002",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert response.status_code == 403
        assert "no está asignado" in response.json()["error"]["message"]


def test_get_batch_trace_not_found(
    client: TestClient, buyer_id: uuid.UUID, buyer_token: str
) -> None:
    with patch(
        "agrosense_api.api.v1.endpoints.traceability.get_batch_trace",
        side_effect=TraceabilityNotFoundError("Lote de exportación no encontrado"),
    ):
        response = client.get(
            "/api/v1/traceability/LOTE-INEXISTENTE",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
        assert response.status_code == 404
        assert "no encontrado" in response.json()["error"]["message"]


def test_traceability_sets_rls_buyer_context(
    client: TestClient, buyer_id: uuid.UUID, buyer_token: str
) -> None:
    """RN-11: cada request de comprador fija app.current_buyer_id (SET LOCAL) antes de consultar."""
    with (
        patch(
            "agrosense_api.api.v1.dependencies.set_buyer_context", new=AsyncMock()
        ) as set_context,
        patch(
            "agrosense_api.api.v1.endpoints.traceability.get_buyer_batches",
            new=AsyncMock(return_value=[]),
        ),
    ):
        response = client.get(
            "/api/v1/traceability/batches",
            headers={"Authorization": f"Bearer {buyer_token}"},
        )
    assert response.status_code == 200
    set_context.assert_awaited_once()
    assert set_context.await_args.args[1] == buyer_id


@pytest.mark.anyio
async def test_get_batch_trace_hidden_by_rls_but_existing_is_forbidden() -> None:
    """CA-09: si RLS oculta el lote pero existe (export_batch_exists = true) -> 403."""
    session = AsyncMock()
    session.scalar.side_effect = [None, True]
    with pytest.raises(TraceabilityForbiddenError):
        await get_batch_trace(session, "LOTE-OTRO-002", uuid.uuid4())
    assert session.scalar.await_count == 2


@pytest.mark.anyio
async def test_get_batch_trace_missing_batch_is_not_found() -> None:
    session = AsyncMock()
    session.scalar.side_effect = [None, False]
    with pytest.raises(TraceabilityNotFoundError):
        await get_batch_trace(session, "LOTE-INEXISTENTE", uuid.uuid4())
