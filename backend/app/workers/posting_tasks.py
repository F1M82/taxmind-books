"""Celery tasks for Tally posting."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.core.database import SessionLocal

# Late lookup for the dispatcher: a test that mocks
# voucher_dispatcher.dispatch_voucher_to_tally expects the worker to
# pick up the patch. Exception classes are class identity — direct
# import is fine. See CONNECTOR_PROTOCOL.md §"Patchable singletons".
from app.services.tally import voucher_dispatcher as _voucher_dispatcher_mod
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers.posting_tasks")


@celery_app.task(
    bind=True,
    name="app.workers.post_voucher_to_tally",
    autoretry_for=(ConnectorOffline, CommandTimeout),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def post_voucher_to_tally(  # type: ignore[no-untyped-def]
    self,
    voucher_id: str,
    company_id: str,
    user_id: str | None,
    request_id: str,
):
    """Run one async-dispatch on a fresh DB session."""
    db = SessionLocal()
    try:
        asyncio.run(
            _voucher_dispatcher_mod.dispatch_voucher_to_tally(
                db=db,
                voucher_id=UUID(voucher_id),
                company_id=UUID(company_id),
                user_id=UUID(user_id) if user_id else None,
                request_id=UUID(request_id),
            )
        )
        db.commit()
    except (ConnectorOffline, CommandTimeout):
        db.commit()  # audit row was emitted; commit and let Celery retry
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
