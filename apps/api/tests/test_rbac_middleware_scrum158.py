"""Tests unitarios y de integracion para SCRUM-158:
Role-Based Access Control (RBAC) Middleware / Dependencies for FastAPI Endpoints.

Valida:
1. Validacion de roles permitidos con require_roles dependency factory.
2. Acceso permitido cuando el rol coincide (ej. ADMIN, QUALITY_CHIEF, OPERATOR).
3. Rechazo HTTP 403 Forbidden cuando el rol no esta autorizado.
4. Extraccion y propagacion del rol en el payload del token JWT y el modelo AuthenticatedUser.
5. Integracion de RBAC en endpoints de FastAPI.
"""

import pytest
from agrosense_api.api.v1.dependencies import get_current_user, require_roles
from agrosense_api.core.config import get_settings
from agrosense_api.core.security import create_access_token, hash_password
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.script import app
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "super_secret_jwt_key_at_least_32_characters_long"  # noqa: S105


@pytest.fixture(autouse=True)
def setup_auth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGROSENSE_JWT_SECRET_KEY", TEST_JWT_SECRET)
    monkeypatch.setenv("AGROSENSE_AUTH_USERNAME", "admin@agrosense.com")
    hashed = hash_password("SuperSecret123!")  # noqa: S106
    monkeypatch.setenv("AGROSENSE_AUTH_PASSWORD_HASH", hashed)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==========================================
# 1. Unit Tests for require_roles Dependency
# ==========================================


@pytest.mark.anyio
async def test_require_roles_grants_access_for_authorized_role() -> None:
    checker = require_roles("ADMIN", "QUALITY_CHIEF")
    user = AuthenticatedUser(
        username="admin@agrosense.com",
        role="ADMIN",
        scopes=["lots:read"],
    )

    result = await checker(current_user=user)
    assert result.role == "ADMIN"
    assert result.username == "admin@agrosense.com"


@pytest.mark.anyio
async def test_require_roles_denies_access_for_unauthorized_role() -> None:
    checker = require_roles("ADMIN")
    user = AuthenticatedUser(
        username="operator@agrosense.com",
        role="OPERATOR",
        scopes=["lots:read"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await checker(current_user=user)

    assert exc_info.value.status_code == 403
    assert "Operation not permitted for role 'OPERATOR'" in exc_info.value.detail


# ==========================================
# 2. Integration Tests with FastAPI Routes
# ==========================================


def test_rbac_endpoint_access_granted(client: TestClient) -> None:
    # Creamos un endpoint temporal protegido por require_roles
    @app.get("/api/v1/test-rbac-admin")
    async def admin_only_endpoint(
        user: AuthenticatedUser = Depends(require_roles("ADMIN")),  # noqa: B008
    ) -> dict[str, str]:
        return {"status": "ok", "role": user.role}

    token = create_access_token(subject="admin@agrosense.com", role="ADMIN")
    response = client.get(
        "/api/v1/test-rbac-admin",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "ADMIN"}


def test_rbac_endpoint_access_forbidden(client: TestClient) -> None:
    @app.get("/api/v1/test-rbac-quality")
    async def quality_only_endpoint(
        user: AuthenticatedUser = Depends(require_roles("QUALITY_CHIEF")),  # noqa: B008
    ) -> dict[str, str]:
        return {"status": "ok", "role": user.role}

    # Usuario con rol OPERATOR intenta acceder a ruta de QUALITY_CHIEF
    token = create_access_token(subject="admin@agrosense.com", role="OPERATOR")
    response = client.get(
        "/api/v1/test-rbac-quality",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "Operation not permitted for role 'OPERATOR'" in response.json()["error"]["message"]
