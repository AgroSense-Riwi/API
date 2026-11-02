from collections.abc import Iterator

import pytest
from agrosense_api.api.v1.endpoints import health
from agrosense_api.core.config import get_settings
from agrosense_api.script import app
from fastapi.testclient import TestClient
from openapi_spec_validator import validate


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _patch_checks(monkeypatch: pytest.MonkeyPatch, database: str, rabbitmq: str) -> None:
    async def fake_db() -> str:
        return database

    async def fake_mq() -> str:
        return rabbitmq

    monkeypatch.setattr(health, "check_database", fake_db)
    monkeypatch.setattr(health, "check_rabbitmq", fake_mq)


def test_health_check_is_public_and_reports_dependencies(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_checks(monkeypatch, "connected", "connected")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "database": "connected",
        "rabbitmq": "connected",
        "storage": "not_checked",
    }


@pytest.mark.parametrize(
    ("database", "rabbitmq"),
    [("disconnected", "connected"), ("connected", "disconnected"), ("connected", "not_configured")],
)
def test_health_check_returns_503_when_a_dependency_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, database: str, rabbitmq: str
) -> None:
    _patch_checks(monkeypatch, database, rabbitmq)
    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["database"] == database
    assert body["rabbitmq"] == rabbitmq


def test_liveness_does_not_depend_on_database_or_broker(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_checks(monkeypatch, "disconnected", "disconnected")
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "agrosense-api"}


@pytest.mark.anyio
async def test_rabbitmq_check_detects_closed_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGROSENSE_RABBITMQ_URL", "amqp://user:pass@127.0.0.1:1/%2Fagrosense")
    get_settings.cache_clear()
    try:
        assert await health.check_rabbitmq() == "disconnected"
    finally:
        get_settings.cache_clear()


def test_openapi_contract_is_valid_openapi_30(client: TestClient) -> None:
    response = client.get("/openapi.json")
    schema = response.json()
    validate(schema)

    assert schema["openapi"] == "3.0.3"
    assert "/docs" in client.get("/docs").text
    assert "/redoc" in client.get("/redoc").text
    paths = schema["paths"]
    assert "/health" in paths
    assert "/health/live" in paths
    assert "/api/v1/telemetry" in paths
    assert "/api/v1/auth/token" in paths
