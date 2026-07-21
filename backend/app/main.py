"""FastAPI application factory.

Phase 0 ships only the placeholder `/` endpoint and `/health`. v1 routes
land via `app.api.v1.router` starting in P0.14.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.api.errors import install_error_handlers
from app.api.v1.router import api_v1
from app.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.money import configure_decimal_context

logger = logging.getLogger("app.main")


async def _reenqueue_sweep_loop(interval_seconds: int) -> None:
    """Periodically re-dispatch retryable-class stranded vouchers (BUG-002).

    Runs inside the API process so it can reach the process-local
    connector registry. Each pass opens its own ``SessionLocal`` and
    sweeps all companies. Exceptions never break the loop.
    """
    from app.core.database import SessionLocal
    from app.services.tally.voucher_reenqueue import (
        reenqueue_retryable_vouchers,
    )

    while True:
        await asyncio.sleep(interval_seconds)
        db = SessionLocal()
        try:
            await reenqueue_retryable_vouchers(db, company_id=None)
        except Exception:
            logger.exception("periodic re-enqueue sweep failed")
        finally:
            db.close()


def _build_lifespan(settings: Settings):  # type: ignore[no-untyped-def]
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if (
            settings.TALLY_REENQUEUE_SWEEP_ENABLED
            and not settings.TAXMIND_SKIP_TALLY_DISPATCH
        ):
            logger.info(
                "starting voucher re-enqueue sweep (every %ss)",
                settings.TALLY_REENQUEUE_SWEEP_INTERVAL_SECONDS,
            )
            task = asyncio.create_task(
                _reenqueue_sweep_loop(
                    settings.TALLY_REENQUEUE_SWEEP_INTERVAL_SECONDS
                )
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan


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
        lifespan=_build_lifespan(settings),
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
