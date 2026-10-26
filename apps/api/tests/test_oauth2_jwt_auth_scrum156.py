"""Unit and integration tests for SCRUM-156:
OAuth2 JWT Authentication Provider and Password Hashing Module.

Validates:
1. Password hashing with argon2 (cryptographic security, verification, invalid password rejection).
2. JWT Access Token generation (HS256, claims sub, iss, scope, iat, exp).
3. JWT Access Token decoding, expiration, and invalid secret/tampered signature handling.
4. Buyer Access Token generation and decoding (buyer_id claim, custom/default scopes).
5. OAuth2 /api/v1/auth/token endpoint (success with credentials, rejection on invalid password,
   rejection on bad username, 503 if unconfigured).
6. get_current_user dependency with SecurityScopes verification.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
import pytest
from agrosense_api.api.v1.dependencies import get_current_user
from agrosense_api.core.config import get_settings
from agrosense_api.core.security import (
    create_access_token,
    create_buyer_access_token,
    decode_access_token,
    decode_buyer_access_token,
    hash_password,
    verify_password,
)
from agrosense_api.schemas.auth import AuthenticatedUser
from agrosense_api.script import app
from fastapi import HTTPException
from fastapi.security.oauth2 import SecurityScopes
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "super_secret_jwt_key_at_least_32_characters_long"  # noqa: S105
TEST_BUYER_SECRET = "super_secret_buyer_jwt_key_at_least_32_chars_long"  # noqa: S105


@pytest.fixture(autouse=True)
def setup_auth_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use argon2 for tests
    monkeypatch.setenv("AGROSENSE_JWT_SECRET_KEY", TEST_JWT_SECRET)
    monkeypatch.setenv("AGROSENSE_BUYER_JWT_SECRET_KEY", TEST_BUYER_SECRET)
    monkeypatch.setenv("AGROSENSE_AUTH_USERNAME", "admin@agrosense.com")
    # Pre-hashed argon2 password for 'SuperSecret123!'
    hashed = hash_password("SuperSecret123!")
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
# 1. Password Hashing & Verification Tests
# ==========================================


def test_password_hashing_and_verification() -> None:
    raw_password = "SecurePassword2026!"
    hashed = hash_password(raw_password)

    assert hashed != raw_password
    assert hashed.startswith("$argon2")

    # Correct password verifies
    assert verify_password(raw_password, hashed) is True

    # Incorrect password fails
    assert verify_password("WrongPassword!", hashed) is False
    assert verify_password("", hashed) is False


# ==========================================
# 2. JWT Access Token Provider Tests
# ==========================================


def test_create_and_decode_access_token() -> None:
    username = "admin@agrosense.com"
    token = create_access_token(username)

    assert isinstance(token, str)
    claims = decode_access_token(token)

    assert claims["sub"] == username
    assert claims["iss"] == "agrosense-api"
    assert "lots:read" in claims["scope"]
    assert "iat" in claims
    assert "exp" in claims
    assert claims["exp"] > claims["iat"]


def test_decode_access_token_tampered_fails() -> None:
    token = create_access_token("admin@agrosense.com")
    tampered_token = token[:-5] + "aaaaa"

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered_token)


def test_decode_access_token_expired_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = TEST_JWT_SECRET
    now = datetime.now(UTC) - timedelta(hours=2)
    expired_claims = {
        "sub": "admin@agrosense.com",
        "iss": "agrosense-api",
        "scope": "lots:read",
        "iat": now - timedelta(minutes=30),
        "exp": now,
    }
    expired_token = jwt.encode(expired_claims, secret, algorithm="HS256")

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token)


# ==========================================
# 3. Buyer JWT Access Token Tests
# ==========================================


def test_create_and_decode_buyer_access_token() -> None:
    buyer_uuid = uuid4()
    custom_scopes = ["traceability:read", "export:download"]
    token = create_buyer_access_token(buyer_uuid, scopes=custom_scopes)

    claims = decode_buyer_access_token(token)
    assert claims["sub"] == str(buyer_uuid)
    assert claims["buyer_id"] == str(buyer_uuid)
    assert claims["iss"] == "agrosense-buyer-auth"
    assert set(claims["scope"].split()) == set(custom_scopes)


# ==========================================
# 4. OAuth2 Token Endpoint Integration Tests
# ==========================================


def test_issue_token_success(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/token",
        data={
            "username": "admin@agrosense.com",
            "password": "SuperSecret123!",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"  # noqa: S105

    # Verify that the generated token is decodable
    claims = decode_access_token(data["access_token"])
    assert claims["sub"] == "admin@agrosense.com"


def test_issue_token_wrong_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/token",
        data={
            "username": "admin@agrosense.com",
            "password": "InvalidPassword!",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


def test_issue_token_wrong_username(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/token",
        data={
            "username": "unknown@agrosense.com",
            "password": "SuperSecret123!",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


def test_issue_token_unconfigured_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGROSENSE_AUTH_USERNAME", "")
    get_settings.cache_clear()

    response = client.post(
        "/api/v1/auth/token",
        data={
            "username": "admin@agrosense.com",
            "password": "SuperSecret123!",
        },
    )
    assert response.status_code == 503
    assert "Authentication is not configured" in response.json()["detail"]


# ==========================================
# 5. Dependency & RBAC / Scope Validation Tests
# ==========================================


@pytest.mark.anyio
async def test_get_current_user_valid_token_and_scope() -> None:
    token = create_access_token("admin@agrosense.com")
    security_scopes = SecurityScopes(scopes=["lots:read"])

    user = await get_current_user(token=token, security_scopes=security_scopes)
    assert isinstance(user, AuthenticatedUser)
    assert user.username == "admin@agrosense.com"
    assert "lots:read" in user.scopes


@pytest.mark.anyio
async def test_get_current_user_insufficient_permissions() -> None:
    token = create_access_token("admin@agrosense.com")
    security_scopes = SecurityScopes(scopes=["admin:all_powerful"])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token=token, security_scopes=security_scopes)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Insufficient permissions"


@pytest.mark.anyio
async def test_get_current_user_invalid_token() -> None:
    security_scopes = SecurityScopes(scopes=[])

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token="invalid.bearer.token", security_scopes=security_scopes)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired access token"
