from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from agrosense_api.core.config import get_settings

# Roles internos definidos en data_structure.md (tabla users).
ROLE_AGRONOMIST = "AGRONOMIST"
ROLE_QUALITY_CHIEF = "QUALITY_CHIEF"
ROLE_SUSTAINABILITY_COMMITTEE = "SUSTAINABILITY_COMMITTEE"
ROLE_ADMIN = "ADMIN"
OPERATOR_ROLES: frozenset[str] = frozenset(
    {ROLE_AGRONOMIST, ROLE_QUALITY_CHIEF, ROLE_SUSTAINABILITY_COMMITTEE, ROLE_ADMIN}
)

_password_context: Any = None
_dummy_hash: str | None = None


def _get_password_context() -> Any:
    global _password_context
    if _password_context is None:
        try:
            from passlib.context import CryptContext

            _password_context = CryptContext(schemes=["argon2"], deprecated="auto")
        except Exception as exc:
            raise RuntimeError(
                "System crypt or passlib is not available in the current runtime environment"
            ) from exc
    return _password_context


def hash_password(password: str) -> str:
    return _get_password_context().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verifica un secreto contra su hash Argon2. Un hash malformado nunca autentica."""
    try:
        return bool(_get_password_context().verify(password, password_hash))
    except (ValueError, TypeError):
        return False


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Verifica el secreto; si no hay hash, ejecuta igualmente una verificación ficticia.

    Mantiene un tiempo de respuesta similar exista o no la identidad, para no filtrar
    qué usuarios / client_id existen por diferencias de tiempo.
    """
    global _dummy_hash
    if password_hash:
        return verify_password(password, password_hash)
    if _dummy_hash is None:
        _dummy_hash = hash_password("agrosense-timing-equalizer")
    verify_password(password, _dummy_hash)
    return False


def create_access_token(
    subject: str,
    role: str,
    scopes: list[str] | None = None,
) -> str:
    if role not in OPERATOR_ROLES:
        raise ValueError(f"Unknown operator role '{role}'")
    settings = get_settings()
    secret = settings.jwt_secret_key.get_secret_value()
    if not secret:
        raise RuntimeError("AGROSENSE_JWT_SECRET_KEY must be configured")

    now = datetime.now(UTC)
    active_scopes = scopes if scopes is not None else settings.auth_scopes
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iss": settings.jwt_issuer,
        "scope": " ".join(active_scopes),
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


def create_buyer_access_token(buyer_id: Any, scopes: list[str] | None = None) -> str:
    settings = get_settings()
    secret = settings.buyer_jwt_secret_key.get_secret_value()
    if not secret:
        raise RuntimeError("AGROSENSE_BUYER_JWT_SECRET_KEY must be configured")

    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(buyer_id),
        "buyer_id": str(buyer_id),
        "role": "BUYER",
        "iss": settings.buyer_jwt_issuer,
        "scope": " ".join(scopes or ["traceability:read"]),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_minutes),
    }
    return jwt.encode(claims, secret, algorithm="HS256")
