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

from app.core.audit import AuditContext, AuditEmitter
from app.core.exceptions import LedgerNotSyncedToTally
from app.models.audit_log import AuditLog
from app.models.voucher import Voucher, VoucherStatus
from app.services import notification_service
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

    latest_action = _latest_tally_post_action_subquery(db)

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


# ---------------------------------------------------------------------
# 30-day expiry sweep (v1.3 P0.54)
# ---------------------------------------------------------------------


def _latest_tally_post_action_subquery(db: Session):  # type: ignore[no-untyped-def]
    """DISTINCT ON (entity_id) → the most-recent tally-post audit action
    per voucher. Shared by the re-enqueue and expiry selections; Postgres
    native (the test + CI DBs are Postgres)."""
    return (
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


def select_expired_strands(
    db: Session,
    *,
    company_id: UUID | None = None,
    window: timedelta = REENQUEUE_WINDOW,
    now: datetime | None = None,
) -> list[Voucher]:
    """Return the vouchers eligible for 30-day expiry (P0.54).

    The window complement of `select_retryable_strands`: a voucher
    qualifies when

      * ``status == pending_tally_post``;
      * its most recent tally-post audit action is
        ``voucher.tally_post_queued`` (retryable class — rejection- and
        blocked-class strands keep waiting for their operator action);
      * ``tally_post_queued_at`` is OLDER than ``window`` (30 days).

    Deliberately NOT bounded by `MAX_REENQUEUE_ATTEMPTS` — expiry is
    about the wait, not the attempt count. Rejection/blocked strands are
    excluded: they never belonged to the auto-retry queue and are
    already surfaced with an error for manual handling.
    """
    now = now or datetime.now(UTC)
    cutoff = now - window

    latest_action = _latest_tally_post_action_subquery(db)

    query = (
        db.query(Voucher)
        .join(latest_action, latest_action.c.voucher_id == Voucher.id)
        .filter(
            Voucher.status == VoucherStatus.pending_tally_post,
            latest_action.c.action == _RETRYABLE_ACTION,
            Voucher.tally_post_queued_at.isnot(None),
            Voucher.tally_post_queued_at < cutoff,
        )
        .order_by(Voucher.tally_post_queued_at)
    )
    if company_id is not None:
        query = query.filter(Voucher.company_id == company_id)

    return query.all()


def expire_stranded_vouchers(
    db: Session,
    *,
    company_id: UUID | None = None,
    window: timedelta = REENQUEUE_WINDOW,
    now: datetime | None = None,
) -> int:
    """Mark overdue retryable-class strands ``tally_post_expired``.

    P0.54: a queued voucher older than ``window`` (30 days, matching the
    re-enqueue cutoff) has silently stopped being retried — past the
    window `select_retryable_strands` drops it, and nothing said so. For
    each such strand this flips the status to ``tally_post_expired``,
    emits one ``voucher.tally_post_expired`` audit row (source=worker,
    same emitter convention as the dispatcher), and pushes a
    notification to the voucher's creator via the P0.44
    `notification_service.send_to_user` channel. The voucher never
    reached Tally, so it is not a live book entry: every report filter
    is an explicit ``status.in_([posted, pending_tally_post])``
    allow-list, so expired rows drop out of the books by construction.

    Each voucher is committed on its own so one failure can't poison the
    sweep; the notification is best-effort and never blocks the next
    voucher. Returns the count expired. Idempotent: an expired voucher
    no longer matches the selection, so re-running the sweep is a no-op
    and never double-notifies.
    """
    strands = select_expired_strands(
        db, company_id=company_id, window=window, now=now
    )
    if not strands:
        return 0

    logger.info(
        "expiry: %d overdue strand(s) for company=%s (window=%s)",
        len(strands),
        company_id or "ALL",
        window,
    )

    audit = AuditEmitter(
        db,
        AuditContext(
            company=None,  # company_id_override per row, as the dispatcher does
            user=None,
            ip_address=None,
            user_agent="voucher-expiry-sweep/1.0",
            request_id=uuid4(),
            source="worker",
        ),
    )
    window_days = int(window.total_seconds() // 86400)

    expired = 0
    for voucher in strands:
        vid = voucher.id
        vcompany = voucher.company_id
        try:
            voucher.status = VoucherStatus.tally_post_expired
            audit.emit(
                action="voucher.tally_post_expired",
                entity_type="voucher",
                entity_id=vid,
                old_value={"status": VoucherStatus.pending_tally_post.value},
                new_value={
                    "status": VoucherStatus.tally_post_expired.value,
                    "queued_at": (
                        voucher.tally_post_queued_at.isoformat()
                        if voucher.tally_post_queued_at is not None
                        else None
                    ),
                    "window_days": window_days,
                },
                actor_user_id=None,  # no human actor — the sweep decided
                company_id_override=vcompany,
            )
            db.commit()
            expired += 1
        except Exception:
            db.rollback()
            logger.exception("expiry: voucher %s could not be expired", vid)
            continue

        _notify_voucher_expired(db, voucher)

    if expired:
        logger.info("expiry: %d voucher(s) marked tally_post_expired", expired)
    return expired


def _notify_voucher_expired(db: Session, voucher: Voucher) -> None:
    """Best-effort push to the voucher's creator (P0.44 channel).

    Never raises — a notification failure must not roll back the
    committed status change + audit row. No active device tokens is a
    normal no-op (single-founder pilot usually has the phone around, but
    nothing depends on delivery).
    """
    if voucher.created_by is None:
        return
    label = voucher.voucher_number or str(voucher.id)[:8]
    try:
        notification_service.send_to_user(
            db,
            user_id=voucher.created_by,
            notification=notification_service.Notification(
                title="Voucher expired from the Tally queue",
                body=(
                    f"Voucher {label} waited {REENQUEUE_WINDOW.days} days "
                    "without reaching Tally and left the retry queue — "
                    "review it."
                ),
                data={
                    "kind": "voucher.tally_post_expired",
                    "voucher_id": str(voucher.id),
                    "company_id": str(voucher.company_id),
                    "status": VoucherStatus.tally_post_expired.value,
                },
            ),
        )
    except Exception:
        logger.exception(
            "expiry: notification for voucher %s failed", voucher.id
        )
