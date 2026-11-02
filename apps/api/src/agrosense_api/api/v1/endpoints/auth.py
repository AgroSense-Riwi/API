import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agrosense_api.core.config import get_settings
from agrosense_api.core.rate_limit import limiter
from agrosense_api.core.security import create_access_token, create_buyer_access_token
from agrosense_api.db.session import get_db
from agrosense_api.schemas.auth import TokenResponse
from agrosense_api.services.auth import authenticate_buyer, authenticate_operator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


class BuyerTokenRequest(BaseModel):
    client_id: str = Field(
        min_length=1, max_length=80, description="Identificador público del comprador"
    )
    api_key: str = Field(
        min_length=1, max_length=256, description="Secreto API emitido al comprador"
    )


def _auth_backend_unavailable(exc: Exception) -> HTTPException:
    logger.exception("Authentication backend unavailable", exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication backend is unavailable",
    )


@router.post("/token", response_model=TokenResponse)
@limiter.limit("5/minute")
async def issue_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    settings = get_settings()
    if not settings.jwt_secret_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    try:
        operator = await authenticate_operator(session, form_data.username, form_data.password)
    except SQLAlchemyError as exc:
        raise _auth_backend_unavailable(exc) from exc
    if operator is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(operator.username, role=operator.role))


@router.post(
    "/buyer-token",
    response_model=TokenResponse,
    summary="Generar token de comprador",
    description=(
        "Intercambia las credenciales del comprador (client_id + api_key, RNF-06) por un JWT "
        "de corta duración con alcance traceability:read sobre sus propios lotes."
    ),
)
@limiter.limit("10/minute")
async def issue_buyer_token(
    request: Request,
    payload: BuyerTokenRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    settings = get_settings()
    if not settings.buyer_jwt_secret_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Buyer authentication is not configured",
        )
    try:
        buyer_id = await authenticate_buyer(session, payload.client_id, payload.api_key)
    except SQLAlchemyError as exc:
        raise _auth_backend_unavailable(exc) from exc
    if buyer_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_buyer_access_token(buyer_id))
