"""Autenticación de operadores internos (tabla users) y compradores (client credentials)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.core.config import get_settings
from agrosense_api.core.security import (
    OPERATOR_ROLES,
    ROLE_ADMIN,
    verify_password_or_dummy,
)
from agrosense_api.models.buyer import InternationalBuyer
from agrosense_api.models.cupping import User


@dataclass(frozen=True, slots=True)
class AuthenticatedOperator:
    username: str
    role: str


async def authenticate_operator(
    session: AsyncSession, username: str, password: str
) -> AuthenticatedOperator | None:
    """Valida usuario/contraseña de un operador interno.

    1. Usuario bootstrap configurado por entorno (AGROSENSE_AUTH_USERNAME /
       AGROSENSE_AUTH_PASSWORD_HASH): sólo para el primer arranque; recibe ADMIN explícito.
    2. Tabla ``users`` (email + password_hash Argon2, is_active) con el rol real del usuario.
    """
    settings = get_settings()
    bootstrap_hash = settings.auth_password_hash
    if (
        settings.auth_username
        and bootstrap_hash is not None
        and bootstrap_hash.get_secret_value()
        and hmac.compare_digest(username.encode(), settings.auth_username.encode())
    ):
        if verify_password_or_dummy(password, bootstrap_hash.get_secret_value()):
            return AuthenticatedOperator(username=settings.auth_username, role=ROLE_ADMIN)
        return None

    user = await session.scalar(
        select(User).where(func.lower(User.email) == username.strip().lower())
    )
    password_hash = user.password_hash if user is not None else None
    if not verify_password_or_dummy(password, password_hash):
        return None
    if user is None or not user.is_active or user.role not in OPERATOR_ROLES or not user.email:
        return None
    return AuthenticatedOperator(username=user.email, role=user.role)


async def authenticate_buyer(session: AsyncSession, client_id: str, api_key: str) -> UUID | None:
    """Valida client_id + api_key de un comprador (RNF-06). Devuelve su buyer_id o None."""
    buyer = await session.scalar(
        select(InternationalBuyer).where(InternationalBuyer.client_id == client_id)
    )
    api_key_hash = buyer.api_key_hash if buyer is not None else None
    if not verify_password_or_dummy(api_key, api_key_hash):
        return None
    if buyer is None or not buyer.is_active:
        return None
    return buyer.buyer_id
