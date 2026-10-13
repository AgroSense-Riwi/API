from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str
    scopes: list[str] = Field(default_factory=list)
