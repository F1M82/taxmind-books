"""Re-enqueue retryable-class stranded vouchers (BUG-Books-002).

Problem
-------
A voucher whose Tally post fails *retryably* (connector offline, command
timeout, or a `retryable=True` envelope) stays in `pending_tally_post`
with a `voucher.tally_post_queued` audit row. In eager / single-instance
mode nothing re-attempts it after the connector comes back — the row is
stranded, counted in the books but never posted (BUG-002).

Why this lives in the API process
---------------------------------
The connector registry is process-local (`connector_registry.py` — Redis
fan-out is the deferred BUG-003 Direction B). A Celery-beat sweep runs in
the worker process, which can't see the registry, so it would only ever
raise `ConnectorOffline`. Re-dispatch therefore has to run inside the
uvicorn process that owns the WebSocket. Two triggers call the same core
here:

  * connector-up event — `_handle_register` in `connector_ws.py`, when a
    connector (re)registers with Tally running;
  * periodic sweep — a lifespan-started asyncio loop in `main.py`.

Scope: retryable-class only
---------------------------
Only strands whose **latest** tally-post audit action is
`voucher.tally_post_queued` are re-enqueued. Rejection-class
(`voucher.tally_post_failed`) and unsynced-class
(`voucher.tally_post_blocked`) strands need operator action, not an
auto-retry, so they are excluded (the 2026-05-22 scope-widening in the
BUG-002 note). All three classes leave the row at `pending_tally_post`,
so the row alone can't distinguish them — the audit trail is the signal.

Re-dispatch reuses `dispatch_voucher_to_tally` with
`idempotency_key=str(voucher_id)`, so the connector-side idempotency
cache (shipped 2026-06-14) dedups a voucher Tally already accepted — a
re-enqueue can never double-post.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.exceptions import LedgerNotSyncedToTally
from app.models.audit_log import AuditLog
from app.models.voucher import Voucher, VoucherStatus
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
    ConnectorRegistry,
    TallyRejectedEnvelope,
    TallyRetryableEnvelope,
)
from app.services.tally.voucher_dispatcher import dispatch_voucher_to_tally

logger = logging.getLogger("app.services.tally.voucher_reenqueue")

# The audit actions the dispatcher emits on a tally-post attempt. The
# *latest* one for a voucher determines its class.
_TALLY_POST_ACTIONS: tuple[str, ...] = (
    "voucher.tally_post_queued",  # retryable — the ONLY re-enqueueable class
    "voucher.tally_post_failed",  # rejection — operator action
    "voucher.tally_post_blocked",  # unsynced ledgers — operator action
    "voucher.posted_to_tally",  # done
    "voucher.posted_as_optional",  # done
)
_RETRYABLE_ACTION = "voucher.tally_post_queued"

# A strand that keeps failing retryably must eventually stop being swept
# (a persistently-offline company should not accrue unbounded attempts).
MAX_REENQUEUE_ATTEMPTS = 12
# Matches the P0.54 `pending_tally_post` 30-day expiry window.
REENQUEUE_WINDOW = timedelta(days=30)

# Exceptions dispatch_voucher_to_tally raises that are "handled" — the
# audit row is already emitted, so we commit and move on rather than
# rolling back the whole sweep.
_HANDLED_DISPATCH_ERRORS = (
    ConnectorOffline,
    CommandTimeout,
    TallyRetryableEnvelope,
    TallyRejectedEnvelope,
    # The dispatcher emits `voucher.tally_post_blocked` before raising
    # this; commit (don't roll back) so the reclassification sticks and
    # the strand drops out of future retryable sweeps.
    LedgerNotSyncedToTally,
)


def select_retryable_strands(
    db: Session,
    *,
    company_id: UUID | None = None,
    max_attempts: int = MAX_REENQUEUE_ATTEMPTS,
    window: timedelta = REENQUEUE_WINDOW,
    now: datetime | None = None,
) -> list[Voucher]:
    """Return the vouchers eligible for retryable-class re-enqueue.

    A voucher qualifies when all hold:

      * ``status == pending_tally_post``;
      * its most recent tally-post audit action is
        ``voucher.tally_post_queued`` (retryable class — not rejected,
        blocked, or already posted);
      * ``tally_post_attempts < max_attempts`` (bounded retry);
      * ``tally_post_queued_at`` is within ``window`` (matches expiry).

    ``company_id`` scopes to one company (the connector-up trigger);
    ``None`` sweeps all companies (the periodic trigger). The session is
    expected to be an unscoped ``SessionLocal`` — this runs outside any
    request's tenant scope.
    """
    now = now or datetime.now(UTC)
    cutoff = now - window

    # DISTINCT ON (entity_id) ORDER BY entity_id, created_at DESC → the
    # single most-recent tally-post audit action per voucher. Postgres
    # native; the test + CI DBs are Postgres.
    latest_action = (
        db.query(
            AuditLog.entity_id.label("voucher_id"),
            AuditLog.action.label("action"),
        )
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.action.in_(_TALLY_POST_ACTIONS),
        )
        .distinct(AuditLog.entity_id)
        .order_by(
            AuditLog.entity_id,
            AuditLog.created_at.desc(),
            AuditLog.id.desc(),
        )
        .subquery()
    )

    query = (
        db.query(Voucher)
        .join(latest_action, latest_action.c.voucher_id == Voucher.id)
        .filter(
            Voucher.status == VoucherStatus.pending_tally_post,
            latest_action.c.action == _RETRYABLE_ACTION,
            Voucher.tally_post_attempts < max_attempts,
            Voucher.tally_post_queued_at.isnot(None),
            Voucher.tally_post_queued_at >= cutoff,
        )
        .order_by(Voucher.tally_post_queued_at)
    )
    if company_id is not None:
        query = query.filter(Voucher.company_id == company_id)

    return query.all()


async def reenqueue_retryable_vouchers(
    db: Session,
    *,
    company_id: UUID | None = None,
    registry: ConnectorRegistry | None = None,
    max_attempts: int = MAX_REENQUEUE_ATTEMPTS,
    window: timedelta = REENQUEUE_WINDOW,
    now: datetime | None = None,
) -> int:
    """Re-dispatch every eligible retryable strand; return the count re-posted.

    Each strand is dispatched independently and committed on its own so
    one failure can't poison the rest. ``dispatch_voucher_to_tally``
    emits the audit row for both success and handled-failure, so a
    handled failure is committed (not rolled back) and simply not counted
    as a success. Uses ``idempotency_key=str(voucher_id)`` (inside the
    dispatcher) so a re-post can never duplicate in Tally.
    """
    strands = select_retryable_strands(
        db,
        company_id=company_id,
        max_attempts=max_attempts,
        window=window,
        now=now,
    )
    if not strands:
        return 0

    logger.info(
        "reenqueue: %d retryable strand(s) for company=%s",
        len(strands),
        company_id or "ALL",
    )

    posted = 0
    for voucher in strands:
        vid = voucher.id
        vcompany = voucher.company_id
        try:
            await dispatch_voucher_to_tally(
                db=db,
                voucher_id=vid,
                company_id=vcompany,
                user_id=voucher.created_by,
                request_id=uuid4(),
                registry=registry,
            )
            db.commit()
            posted += 1
        except _HANDLED_DISPATCH_ERRORS as exc:
            # Audit row already emitted by the dispatcher — persist it.
            db.commit()
            logger.info(
                "reenqueue: voucher %s still not posted (%s)",
                vid,
                exc.__class__.__name__,
            )
        except Exception:
            db.rollback()
            logger.exception("reenqueue: voucher %s dispatch errored", vid)

    if posted:
        logger.info("reenqueue: %d voucher(s) posted to Tally", posted)
    return posted
