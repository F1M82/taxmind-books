"""FastAPI application factory.

Phase 0 ships only the placeholder `/` endpoint and `/health`. v1 routes
land via `app.api.v1.router` starting in P0.14.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.v1.router import api_v1
from app.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.money import configure_decimal_context


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI app.

    Pass `settings` only from tests that need to override the live env;
    production callers leave it `None` to load from the process
    environment via `get_settings()`.
    """
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)
    configure_decimal_context()

    app = FastAPI(
        title="TaxMind Books API",
        version="0.1.0",
        description="Backend API for TaxMind Books — Phase 0 skeleton",
    )
    install_error_handlers(app)
    app.include_router(api_v1)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "running"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.APP_ENV}

    return app


app = create_app()
