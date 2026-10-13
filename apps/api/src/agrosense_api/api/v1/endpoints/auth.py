from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from agrosense_api.core.config import get_settings
from agrosense_api.core.rate_limit import limiter
from agrosense_api.core.security import create_access_token, verify_password
from agrosense_api.schemas.auth import TokenResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/token", response_model=TokenResponse)
@limiter.limit("5/minute")
async def issue_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    settings = get_settings()
    password_hash = settings.auth_password_hash
    if (
        not settings.auth_username
        or password_hash is None
        or not password_hash.get_secret_value()
        or not settings.jwt_secret_key.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    if form_data.username != settings.auth_username or not verify_password(
        form_data.password, password_hash.get_secret_value()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(form_data.username))
