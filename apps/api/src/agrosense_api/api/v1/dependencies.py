from collections.abc import AsyncIterator, Callable
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2PasswordBearer,
)
from fastapi.security.oauth2 import SecurityScopes
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.core.config import get_settings
from agrosense_api.core.security import (
    OPERATOR_ROLES,
    decode_access_token,
    decode_buyer_access_token,
)
from agrosense_api.db.session import get_db, set_buyer_context, set_operator_context
from agrosense_api.schemas.auth import AuthenticatedUser

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    scopes={
        "lots:read": "Read lot traceability and origin composition",
        "farms:read": "Read farm details and export contribution history",
        "lots:certify": "Certify export lots",
    },
)
buyer_jwt = HTTPBearer(
    scheme_name="BuyerJWT",
    description=(
        "JWT for a buyer. It must contain a buyer_id UUID claim and the traceability:read scope."
    ),
    bearerFormat="JWT",
)


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    security_scopes: SecurityScopes,
) -> AuthenticatedUser:
    settings = get_settings()
    if not settings.jwt_secret_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )
    try:
        claims = decode_access_token(token)
    except (jwt.InvalidTokenError, RuntimeError) as exc:
        raise _invalid_token() from exc

    username = claims.get("sub")
    if not isinstance(username, str) or not username:
        raise _invalid_token()
    # El rol es obligatorio: un token sin rol (o con un rol desconocido) se rechaza en lugar
    # de asumir privilegios por defecto.
    role = claims.get("role")
    if not isinstance(role, str) or role not in OPERATOR_ROLES:
        raise _invalid_token()
    scope_claim = claims.get("scope", "")
    if not isinstance(scope_claim, str):
        raise _invalid_token()
    token_scopes = scope_claim.split()
    if not set(security_scopes.scopes).issubset(token_scopes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return AuthenticatedUser(username=username, role=role, scopes=token_scopes)


def require_roles(*allowed_roles: str) -> Callable[..., AuthenticatedUser]:
    """Dependency factory para control de acceso basado en roles (RBAC).

    Valida que el usuario autenticado posea uno de los roles autorizados.
    Lanza HTTP 403 Forbidden si el rol no coincide.
    """

    async def role_checker(
        current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted for role '{current_user.role}'",
            )
        return current_user

    return role_checker


async def get_current_buyer_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(buyer_jwt)],
) -> UUID:
    settings = get_settings()
    if not settings.buyer_jwt_secret_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Buyer authentication is not configured",
        )
    try:
        claims = decode_buyer_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, RuntimeError) as exc:
        raise _invalid_token() from exc

    buyer_id_claim = claims.get("buyer_id")
    if not isinstance(buyer_id_claim, str):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A buyer identity is required",
        )
    try:
        buyer_id = UUID(buyer_id_claim)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid buyer identity in access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    scope_claim = claims.get("scope", "")
    if not isinstance(scope_claim, str):
        raise _invalid_token()
    token_scopes = set(scope_claim.split())
    if "traceability:read" not in token_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return buyer_id


async def get_buyer_db(
    buyer_id: Annotated[UUID, Depends(get_current_buyer_id)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[AsyncSession]:
    """Sesión de BD con el contexto RLS del comprador (``SET LOCAL app.current_buyer_id``)."""
    await set_buyer_context(session, buyer_id)
    yield session


def get_operator_db(*allowed_roles: str) -> Callable[..., AsyncIterator[AsyncSession]]:
    """Sesión de BD con la ruta RLS de operador, sólo tras validar JWT y rol interno."""

    async def operator_session(
        _user: Annotated[AuthenticatedUser, Depends(require_roles(*allowed_roles))],
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> AsyncIterator[AsyncSession]:
        await set_operator_context(session)
        yield session

    return operator_session
