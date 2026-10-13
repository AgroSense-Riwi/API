from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGROSENSE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "AgroSense Café API"
    database_url: str = "sqlite+aiosqlite:///./agrosense.db"
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    jwt_secret_key: SecretStr = SecretStr("")
    buyer_jwt_secret_key: SecretStr = SecretStr("")
    jwt_access_token_minutes: int = Field(default=30, gt=0)
    jwt_issuer: str = "agrosense-api"
    buyer_jwt_issuer: str = "agrosense-buyer-auth"
    auth_username: str | None = None
    auth_password_hash: SecretStr | None = None
    auth_scopes: list[str] = Field(
        default_factory=lambda: ["lots:read", "farms:read", "lots:certify"]
    )
    rabbitmq_url: SecretStr | None = None
    telemetry_queue: str = "agrosense.telemetry"
    allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("jwt_secret_key", "buyer_jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and len(secret) < 32:
            raise ValueError("JWT signing secret must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
