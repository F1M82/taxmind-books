"""Application settings.

Settings are loaded from environment variables (and `.env` for local dev).
Required variables have no default and will raise a `ValidationError` at
startup if absent — by design, per Constitution §4 ("never suppress errors
silently").
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level configuration for the FastAPI app and Celery workers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------------- Required: database / cache ----------------
    DATABASE_URL: str = Field(
        ...,
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db",
    )
    REDIS_URL: str = Field(..., description="Redis URL for Celery broker and rate limits")

    # ---------------- Required: auth / crypto ----------------
    JWT_SECRET: SecretStr = Field(..., description="Secret for signing JWT access/refresh tokens")
    SECRET_KEY: SecretStr = Field(..., description="App-wide secret for signing/encryption")
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=14, ge=1)

    # ---------------- Required: connector ----------------
    CONNECTOR_JWT_SECRET: SecretStr = Field(
        ..., description="Secret for signing connector enrollment / session tokens"
    )

    # ---------------- Optional: LLM providers (populated in Phase 1) ----------------
    ANTHROPIC_API_KEY: SecretStr | None = None
    OPENAI_API_KEY: SecretStr | None = None

    # ---------------- Optional: object storage (populated in Phase 1) ----------------
    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str | None = None
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY: SecretStr | None = None
    S3_SECRET_KEY: SecretStr | None = None

    # ---------------- Tally ----------------
    TALLY_HOST: str = Field(default="localhost")
    TALLY_PORT: int = Field(default=9000, ge=1, le=65535)
    CONNECTOR_PORT: int = Field(default=8001, ge=1, le=65535)

    # ---------------- CORS pin list ----------------
    WEB_URL: str = Field(default="http://localhost:5173")
    MOBILE_URL: str = Field(default="http://localhost:19006")

    # ---------------- Runtime ----------------
    APP_ENV: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")

    @property
    def cors_origins(self) -> list[str]:
        """Origins allowed by CORS, derived from WEB_URL and MOBILE_URL."""
        return [self.WEB_URL, self.MOBILE_URL]


def get_settings() -> Settings:
    """Construct a `Settings` instance from the current environment.

    Kept as a function (not a module-level singleton) so tests can override
    the environment per-test without import-order surprises.
    """
    return Settings()  # type: ignore[call-arg]
