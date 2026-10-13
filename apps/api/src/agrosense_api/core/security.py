from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from agrosense_api.core.config import get_settings

_password_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_context.verify(password, password_hash)


def create_access_token(subject: str) -> str:
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()
    if not secret:
        raise RuntimeError("AGROSENSE_JWT_SECRET_KEY must be configured")

    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "iss": settings.jwt_issuer,
        "scope": " ".join(settings.auth_scopes),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_minutes),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()
    if not secret:
        raise RuntimeError("AGROSENSE_JWT_SECRET_KEY must be configured")

    return jwt.decode(token, secret, algorithms=["HS256"], issuer=settings.jwt_issuer)


def decode_buyer_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    secret = settings.buyer_jwt_secret_key.get_secret_value()
    if not secret:
        raise RuntimeError("AGROSENSE_BUYER_JWT_SECRET_KEY must be configured")

    return jwt.decode(token, secret, algorithms=["HS256"], issuer=settings.buyer_jwt_issuer)
