"""Connector settings (Pydantic).

Configurable via environment variables and an optional `.env` file
in the connector's working directory.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConnectorSettings(BaseSettings):
    """Top-level configuration for the TaxMind Books connector."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------------- Tally local interface ----------------
    TALLY_HOST: str = Field(default="localhost")
    TALLY_PORT: int = Field(default=9000, ge=1, le=65535)
    TALLY_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    # ---------------- Backend WebSocket ----------------
    BACKEND_WS_URL: str = Field(
        default="wss://api.taxmindbooks.com/api/v1/connector/ws",
        description="WS endpoint the connector dials into.",
    )
    CONNECTOR_TOKEN: SecretStr | None = Field(
        default=None,
        description=(
            "JWT-like signed token issued by the backend at enrollment "
            "(POST /api/v1/connector/enroll). Required at runtime; left "
            "Optional here so the package imports for tests."
        ),
    )
    CONNECTOR_COMPANY_ID: str | None = Field(
        default=None,
        description=(
            "Company UUID the connector dials in for. Routed through "
            "ConnectorSettings (not raw os.environ) so values declared "
            "in `.env` next to the .exe are honoured."
        ),
    )

    # ---------------- Reconnect / heartbeat ----------------
    HEARTBEAT_SECONDS: int = Field(default=30, ge=1)
    RECONNECT_INITIAL_BACKOFF: float = Field(default=1.0, gt=0)
    RECONNECT_MAX_BACKOFF: float = Field(default=60.0, gt=0)

    # ---------------- Runtime ----------------
    LOG_LEVEL: str = Field(default="INFO")


def get_settings() -> ConnectorSettings:
    return ConnectorSettings()  # type: ignore[call-arg]
