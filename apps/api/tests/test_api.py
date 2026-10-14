from collections.abc import Iterator

import pytest
from agrosense_api.script import app
from fastapi.testclient import TestClient
from openapi_spec_validator import validate


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_check_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "agrosense-api"}


def test_openapi_contract_is_valid_openapi_30(client: TestClient) -> None:
    response = client.get("/openapi.json")
    schema = response.json()
    validate(schema)

    assert schema["openapi"] == "3.0.3"
    assert "/docs" in client.get("/docs").text
    assert "/redoc" in client.get("/redoc").text
    paths = schema["paths"]
    assert "/health" in paths
    assert "/api/v1/telemetry" in paths
    assert "/api/v1/auth/token" in paths
