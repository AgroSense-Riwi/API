from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import UUID

import jwt
import pytest
from agrosense_api.api.v1 import dependencies
from agrosense_api.api.v1.dependencies import get_current_buyer_id, get_current_user
from agrosense_api.api.v1.endpoints import auth, farms, lots, soil, telemetry, traceability
from agrosense_api.core import security
from agrosense_api.core.config import Settings
from agrosense_api.core.security import create_access_token, decode_access_token
from agrosense_api.db.session import get_db
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.schemas.farms import FarmResponse
from agrosense_api.schemas.lots import (
    FarmContributionResponse,
    LotCertificateResponse,
    LotResponse,
)
from agrosense_api.schemas.telemetry import TelemetryCreate
from agrosense_api.schemas.traceability import TraceabilityResponse
from agrosense_api.script import app
from agrosense_api.services.messaging import MessageBrokerUnavailable
from agrosense_api.services.traceability import (
    TraceabilityForbiddenError,
    get_batch_trace,
)
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(client: TestClient) -> TestClient:
    async def authenticated_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            username="test-operator",
            scopes=["lots:read", "farms:read", "lots:certify"],
        )

    app.dependency_overrides[get_current_user] = authenticated_user
    return client


@pytest.fixture
def authenticated_buyer_client(client: TestClient) -> TestClient:
    async def authenticated_buyer() -> UUID:
        return UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")

    app.dependency_overrides[get_current_buyer_id] = authenticated_buyer
    return client


def test_health_check_is_public(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "agrosense-api"}


def test_business_routes_require_bearer_token(client: TestClient) -> None:
    response = client.get("/api/v1/visits/priority")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "http_error"


def test_openapi_contains_expected_api_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/api/v1/telemetry" in paths
    assert "/api/v1/traceability/{batch_code}" in paths
    assert "/api/v1/visits/priority" in paths
    assert "/api/v1/soil/recommendations/{farm_id}" in paths
    assert "/api/v1/lots/{lot_id}" in paths
    assert "/api/v1/farms/{farm_id}" in paths
    assert "/api/v1/lots/certify" in paths
    traceability_operation = paths["/api/v1/traceability/{batch_code}"]["get"]
    assert traceability_operation["tags"] == ["Compradores"]
    assert traceability_operation["security"] == [{"BuyerJWT": []}]


def test_openapi_contract_is_valid_openapi_30(client: TestClient) -> None:
    from openapi_spec_validator import validate

    response = client.get("/openapi.json")
    schema = response.json()
    validate(schema)

    assert schema["openapi"] == "3.0.3"
    assert "/docs" in client.get("/docs").text
    assert "/redoc" in client.get("/redoc").text
    assert schema["paths"]["/api/v1/lots/{lot_id}"]["get"]["security"] == [
        {"OAuth2PasswordBearer": ["lots:read"]}
    ]
    assert any(tag["name"] == "Compradores" for tag in schema["tags"])


def test_traceability_endpoint_requires_buyer_token(client: TestClient) -> None:
    response = client.get("/api/v1/traceability/LOT-2026-001")

    assert response.status_code == 401


def test_traceability_returns_only_buyer_authorized_result(
    authenticated_buyer_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    buyer_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    expected = TraceabilityResponse(
        batch_code="LOT-2026-001",
        status="EXPORTABLE_CERTIFICADO",
        farm_id=buyer_id,
        farm_code="FIN-0001",
        farm_name="Hacienda",
        buyer_name="Comprador",
        created_at="2026-10-13T00:00:00Z",
        events=[],
    )

    async def owned_lot(
        _session: object, _batch_code: str, requested_buyer_id: UUID
    ) -> TraceabilityResponse:
        assert requested_buyer_id == buyer_id
        return expected

    monkeypatch.setattr(traceability, "get_batch_trace", owned_lot)
    response = authenticated_buyer_client.get(
        "/api/v1/traceability/LOT-2026-001",
        headers={"Authorization": "Bearer buyer-token"},
    )

    assert response.status_code == 200
    assert response.json()["batch_code"] == "LOT-2026-001"
    assert response.json()["buyer_name"] == "Comprador"


def test_traceability_maps_other_buyers_lot_to_403(
    authenticated_buyer_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden_lot(
        _session: object, _batch_code: str, _buyer_id: UUID
    ) -> TraceabilityResponse:
        raise TraceabilityForbiddenError("This export lot is not assigned to this buyer")

    monkeypatch.setattr(traceability, "get_batch_trace", forbidden_lot)
    response = authenticated_buyer_client.get(
        "/api/v1/traceability/LOT-OTHER-BUYER",
        headers={"Authorization": "Bearer buyer-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "This export lot is not assigned to this buyer"


@pytest.mark.anyio
async def test_buyer_jwt_identity_requires_buyer_claim_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buyer_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    buyer_signing_key = sha256(b"buyer-unit-test-key").hexdigest()
    settings = Settings(buyer_jwt_secret_key=SecretStr(buyer_signing_key))
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    token = jwt.encode(
        {
            "sub": "buyer-test",
            "buyer_id": str(buyer_id),
            "scope": "traceability:read",
            "iss": settings.buyer_jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        buyer_signing_key,
        algorithm="HS256",
    )

    resolved_buyer_id = await get_current_buyer_id(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )

    assert resolved_buyer_id == buyer_id


@pytest.mark.anyio
async def test_buyer_jwt_without_traceability_scope_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buyer_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    buyer_signing_key = sha256(b"buyer-unit-test-key").hexdigest()
    settings = Settings(buyer_jwt_secret_key=SecretStr(buyer_signing_key))
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    token = jwt.encode(
        {
            "sub": "buyer-test",
            "buyer_id": str(buyer_id),
            "scope": "lots:read",
            "iss": settings.buyer_jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        buyer_signing_key,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as error:
        await get_current_buyer_id(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )

    assert error.value.status_code == 403


@pytest.mark.anyio
async def test_operator_signing_key_cannot_mint_buyer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buyer_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    operator_signing_key = sha256(b"operator-unit-test-key").hexdigest()
    settings = Settings(
        jwt_secret_key=SecretStr(operator_signing_key),
        buyer_jwt_secret_key=SecretStr(sha256(b"buyer-unit-test-key").hexdigest()),
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    token = jwt.encode(
        {
            "sub": "buyer-test",
            "buyer_id": str(buyer_id),
            "scope": "traceability:read",
            "iss": settings.jwt_issuer,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        operator_signing_key,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as error:
        await get_current_buyer_id(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )

    assert error.value.status_code == 401


@pytest.mark.anyio
async def test_traceability_service_rejects_lot_owned_by_another_buyer() -> None:
    session = AsyncMock()
    session.scalar.side_effect = [None, UUID("88e42974-a8af-4b91-9d00-c234383bc6db")]

    with pytest.raises(TraceabilityForbiddenError):
        await get_batch_trace(
            session,
            "LOT-OTHER-BUYER",
            UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9"),
        )

    assert "export_batches.buyer_id" in str(session.scalar.await_args_list[0].args[0])
    assert session.scalar.await_count == 2


def test_telemetry_is_queued(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def publish_successfully(_event_id: UUID, telemetry_data: TelemetryCreate) -> None:
        assert telemetry_data.sensor_id == "sensor-1"

    monkeypatch.setattr(telemetry, "publish_telemetry", publish_successfully)
    response = authenticated_client.post(
        "/api/v1/telemetry",
        json={
            "farm_id": "7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9",
            "sensor_id": "sensor-1",
            "metric": "temperature",
            "value": 22.5,
            "unit": "C",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["event_id"]


def test_telemetry_reports_unavailable_broker(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broker_unavailable(_event_id: UUID, _telemetry_data: TelemetryCreate) -> None:
        raise MessageBrokerUnavailable("Telemetry broker is unavailable")

    monkeypatch.setattr(telemetry, "publish_telemetry", broker_unavailable)
    response = authenticated_client.post(
        "/api/v1/telemetry",
        json={
            "farm_id": "7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9",
            "sensor_id": "sensor-1",
            "metric": "temperature",
            "value": 22.5,
            "unit": "C",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Telemetry broker is unavailable"


def test_token_endpoint_issues_bearer_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        auth_username="operator",
        auth_password_hash=SecretStr(
            "$argon2id$v=19$m=65536,t=3,p=4$YWE$z+L/k31RoJ+/njSzQFXSLPjkXygQnL9Kxub42wTxK3w"
        ),
        jwt_secret_key=SecretStr("test-signing-secret-with-32-bytes-minimum"),
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth,
        "verify_password",
        lambda password, _password_hash: password == "test",  # noqa: S105
    )
    monkeypatch.setattr(auth, "create_access_token", lambda _subject: "signed-token")

    response = client.post(
        "/api/v1/auth/token",
        data={"username": "operator", "password": "test"},
    )

    assert response.status_code == 200
    assert response.json() == {"access_token": "signed-token", "token_type": "bearer"}


def test_access_token_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(jwt_secret_key=SecretStr("test-signing-secret-with-32-bytes-minimum"))
    monkeypatch.setattr("agrosense_api.core.security.get_settings", lambda: settings)

    claims = decode_access_token(create_access_token("operator"))

    assert claims["sub"] == "operator"
    assert claims["iss"] == "agrosense-api"
    assert {"lots:read", "farms:read", "lots:certify"}.issubset(set(claims["scope"].split()))


def test_business_route_rejects_missing_scope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        auth_username="operator",
        jwt_secret_key=SecretStr("test-signing-secret-with-32-bytes-minimum"),
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    monkeypatch.setattr(
        dependencies, "decode_access_token", lambda _token: {"sub": "operator", "scope": ""}
    )

    response = client.get(
        "/api/v1/lots/7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Insufficient permissions"


def test_lot_identifier_validation_returns_structured_422(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/api/v1/lots/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"
    assert response.json()["error"]["details"]


def test_lot_endpoint_serializes_pydantic_response(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farm_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    expected = LotResponse(
        id=farm_id,
        batch_code="LOT-2026-001",
        status="EN_PROCESO",
        total_weight_kg="100.00",
        cupping_score="84.00",
        weighted_water_m3_ton=None,
        avg_fertilizer_red_pct=None,
        certified_at=None,
        is_exportable=False,
        is_deforestation_free=False,
        created_at="2026-10-13T00:00:00Z",
        farms=[
            FarmContributionResponse(
                farm_id=farm_id,
                farm_code="FIN-0001",
                farm_name="Hacienda",
                contributed_weight_kg="100.00",
                contribution_pct="100",
            )
        ],
    )

    async def fake_get_lot(session: object, lot_id: UUID) -> LotResponse:
        return expected

    monkeypatch.setattr(lots, "get_lot", fake_get_lot)
    response = authenticated_client.get(f"/api/v1/lots/{farm_id}")

    assert response.status_code == 200
    assert response.json()["batch_code"] == "LOT-2026-001"
    assert response.json()["farms"][0]["contribution_pct"] == "100"


def test_farm_endpoint_serializes_composition(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farm_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    expected = FarmResponse(
        id=farm_id,
        code="FIN-0001",
        name="Hacienda",
        location_geojson=None,
        boundary_geojson=None,
        water_usage_m3_ton="120.00",
        batch_contributions=[],
        total_exported_weight_kg="0",
    )

    async def fake_get_farm(
        session: object, requested_farm_id: UUID, limit: int, offset: int
    ) -> FarmResponse:
        return expected

    monkeypatch.setattr(farms, "get_farm", fake_get_farm)
    response = authenticated_client.get(f"/api/v1/farms/{farm_id}")

    assert response.status_code == 200
    assert response.json()["code"] == "FIN-0001"
    assert response.json()["water_usage_m3_ton"] == "120.00"


def test_certification_route_returns_contract_response(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lot_id = UUID("7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")
    expected = LotCertificateResponse(
        id=lot_id,
        batch_code="LOT-2026-001",
        status="EXPORTABLE_CERTIFICADO",
        total_weight_kg="100.00",
        cupping_score="84.00",
        weighted_water_m3_ton="125.00",
        avg_fertilizer_red_pct="0.00",
        certified_at="2026-10-13T00:00:00Z",
        is_exportable=True,
        is_deforestation_free=True,
        created_at="2026-10-12T00:00:00Z",
        farms=[],
    )

    async def fake_certify_lot(session: object, requested_lot_id: UUID) -> LotCertificateResponse:
        return expected

    monkeypatch.setattr(lots, "certify_lot", fake_certify_lot)
    response = authenticated_client.post("/api/v1/lots/certify", json={"lot_id": str(lot_id)})

    assert response.status_code == 200
    assert response.json()["status"] == "EXPORTABLE_CERTIFICADO"
    assert response.json()["weighted_water_m3_ton"] == "125.00"


def test_missing_lot_returns_structured_404(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing_lot(session: object, lot_id: UUID) -> None:
        return None

    monkeypatch.setattr(lots, "get_lot", missing_lot)
    response = authenticated_client.get("/api/v1/lots/7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Lot not found"


def test_certification_rule_failure_returns_structured_400(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failed_certification(session: object, lot_id: UUID) -> LotCertificateResponse:
        from agrosense_api.services.lots import LotCertificationError

        raise LotCertificationError("RN-08: contributed farm weights must equal lot net weight")

    monkeypatch.setattr(lots, "certify_lot", failed_certification)
    response = authenticated_client.post(
        "/api/v1/lots/certify",
        json={"lot_id": "7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"].startswith("RN-08")


def test_unhandled_error_does_not_leak_exception_details(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unexpected_error(session: object, lot_id: UUID) -> None:
        raise RuntimeError("internal-db-detail")

    monkeypatch.setattr(lots, "get_lot", unexpected_error)
    response = authenticated_client.get("/api/v1/lots/7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9")

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "An internal server error occurred"
    assert "internal-db-detail" not in response.text


def test_soil_route_reports_missing_analysis(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_recommendations(_session: object, _farm_id: UUID) -> None:
        return None

    async def fake_session():
        yield object()

    app.dependency_overrides[get_db] = fake_session
    monkeypatch.setattr(soil, "get_soil_recommendations", no_recommendations)
    response = authenticated_client.get(
        "/api/v1/soil/recommendations/7060dc2f-48eb-5db4-9a7d-47ac1b4b4ae9"
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "No soil analysis found for this farm"
