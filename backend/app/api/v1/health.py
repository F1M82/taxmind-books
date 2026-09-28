"""Readiness probe: `GET /api/v1/health/ready`.

Distinct from the unprefixed liveness probe `GET /health` (`app.main`):
liveness says "the process is up"; readiness says "its dependencies
(Postgres, Redis) answer". Unauthenticated by design, so failure detail
is limited to ``ok`` / ``unavailable`` -- the exception text goes to the
log, never the response.
"""

from __future__ import annotations

import logging

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.core.database import SessionLocal

logger = logging.getLogger("app.api.v1.health")

router = APIRouter(tags=["health"])

_REDIS_TIMEOUT_SECONDS = 2.0


def _check_database() -> str:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        logger.exception("readiness: database check failed")
        return "unavailable"
    finally:
        db.close()


def _check_redis() -> str:
    client = None
    try:
        client = redis.Redis.from_url(
            get_settings().REDIS_URL,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
        )
        client.ping()
        return "ok"
    except Exception:
        logger.exception("readiness: redis check failed")
        return "unavailable"
    finally:
        if client is not None:
            client.close()


@router.get("/health/ready")
def ready() -> JSONResponse:
    database = _check_database()
    redis_status = _check_redis()
    is_ready = database == "ok" and redis_status == "ok"
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "not_ready",
            "database": database,
            "redis": redis_status,
        },
    )
