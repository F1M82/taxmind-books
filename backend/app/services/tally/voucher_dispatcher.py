"""Voucher dispatcher: backend → connector for `post_voucher`.

Two entry points:

  `dispatch_voucher_to_tally(...)` — the async work itself. Loads the
  voucher + ordered entries from DB, builds the `post_voucher.args`
  payload, calls the registry's command, and on success stamps
  `tally_posted_at` / `tally_voucher_guid` + audits
  `voucher.posted_to_tally`. On failure it increments
  `tally_post_attempts` and audits `voucher.tally_post_failed`.

  `enqueue_voucher_post(...)` — the sync hook the API route calls
  right after committing a `voucher.created`. Sends the work to
  Celery via `post_voucher_to_tally.delay(...)`. Tests can swap this
  with a no-op or a synchronous call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import AuditContext, AuditEmitter
from app.core.exceptions import LedgerNotSyncedToTally
from app.models.ledger import Ledger
from app.models.voucher import LedgerEntry, Voucher, VoucherStatus

# Module import for late `get_registry` lookup; the exception classes
# and the ConnectorRegistry type annotation are imported directly
# because tests rely on class identity (for `except`) and types
# aren't monkey-patched. See CONNECTOR_PROTOCOL.md §"Patchable
# singletons" for the rule.
from app.services.tally import connector_registry as _connector_registry_mod
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
    ConnectorRegistry,
    TallyRejectedEnvelope,
    TallyRetryableEnvelope,
)

logger = logging.getLogger("app.services.tally.voucher_dispatcher")


# ---------------------------------------------------------------------
# Pre-flight guard (BUG-005 step 3)
# ---------------------------------------------------------------------


def check_ledgers_synced(
    db: Session,
    *,
    ledger_ids: Iterable[UUID],
    company_id: UUID,
) -> None:
    """Reject voucher operations that reference ledgers not yet synced to Tally.

    Queries the DB fresh on every call — never caches. sync_masters may
    run between an API-layer check and a dispatcher-layer check, so a
    cached result would risk false positives at the boundary.

    Raises:
        LedgerNotSyncedToTally: if any referenced ledger has
            `tally_master_id IS NULL`. The exception's details carry
            `unsynced_ledger_ids`, `unsynced_ledger_names`, and a
            human-readable `remediation` string so both the API 422
            response and the dispatcher audit row can surface the same
            context.
    """
    rows = (
        db.query(Ledger.id, Ledger.name, Ledger.tally_master_id)
        .filter(
            Ledger.id.in_(list(ledger_ids)),
            Ledger.company_id == company_id,
        )
        .all()
    )
    unsynced = [(r.id, r.name) for r in rows if r.tally_master_id is None]
    if not unsynced:
        return

    names = [name for _, name in unsynced]
    rest = len(names) - 1
    msg = (
        f"Ledger {names[0]!r} is not yet synced to Tally"
        + (f" (and {rest} other ledger(s))" if rest else "")
        + ". Run sync_masters before posting vouchers that reference it."
    )
    raise LedgerNotSyncedToTally(
        msg,
        details={
            "unsynced_ledger_ids": [str(lid) for lid, _ in unsynced],
            "unsynced_ledger_names": names,
            "remediation": (
                "Trigger POST /api/v1/connector/sync/{company_id} to "
                "pull ledger masters from Tally, then re-post the voucher."
            ),
        },
    )


# ---------------------------------------------------------------------
# Sync enqueue hook
# ---------------------------------------------------------------------


def enqueue_voucher_post(
    *,
    voucher_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
) -> None:
    """Fire-and-forget dispatch from the API request path.

    Two modes:

    - **Eager / single-process** (`CELERY_TASK_ALWAYS_EAGER=1`): schedule
      the dispatch as an `asyncio.create_task` on the current event
      loop, mirroring the `sync_masters` `_drive()` pattern in
      `app/api/v1/connector.py`. This is the Phase 0 deploy model — the
      connector_registry is process-local, so the dispatcher must run
      in the API uvicorn that owns the WebSocket. See P0.58 / BUG-Books-003.
    - **Worker / multi-process** (eager flag unset): hand off to Celery
      via `post_voucher_to_tally.delay(...)`. Reserved for Phase 0.5+
      once `connector_registry` becomes Redis-pub/sub-backed.

    `TAXMIND_SKIP_TALLY_DISPATCH=1` short-circuits both modes so the test
    suite can run without a live Redis broker or async context.
    """
    settings = get_settings()

    if settings.TAXMIND_SKIP_TALLY_DISPATCH:
        return

    if settings.CELERY_TASK_ALWAYS_EAGER:
        _enqueue_in_process(
            voucher_id=voucher_id,
            company_id=company_id,
            user_id=user_id,
            request_id=request_id,
        )
        return

    from app.workers.posting_tasks import post_voucher_to_tally

    post_voucher_to_tally.delay(
        voucher_id=str(voucher_id),
        company_id=str(company_id),
        user_id=str(user_id) if user_id else None,
        request_id=str(request_id),
    )


def _enqueue_in_process(
    *,
    voucher_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
) -> None:
    """Schedule the dispatch on the current asyncio event loop.

    Returns immediately. The dispatch coroutine opens its own
    `SessionLocal` (the request session is closed by FastAPI on
    response return) and commits independently. On `ConnectorOffline`
    or `CommandTimeout` the dispatch audit row is committed but no
    retry is scheduled — Phase 0 trades retry durability for
    process-locality (P0.58 option 1). The eventual re-enqueue path
    is BUG-Books-002 / Phase 0.5 work.

    Raises `RuntimeError` if called outside a running event loop. The
    only legitimate caller (`enqueue_voucher_post` from
    `vouchers.create_voucher`) is always in an async FastAPI handler;
    a sync caller is a programmer error.
    """
    import asyncio

    from app.core.database import SessionLocal

    async def _drive() -> None:
        db = SessionLocal()
        try:
            await dispatch_voucher_to_tally(
                db=db,
                voucher_id=voucher_id,
                company_id=company_id,
                user_id=user_id,
                request_id=request_id,
            )
            db.commit()
        except (
            ConnectorOffline,
            CommandTimeout,
            TallyRetryableEnvelope,
            TallyRejectedEnvelope,
            LedgerNotSyncedToTally,
        ) as exc:
            # Audit row was emitted by dispatch_voucher_to_tally; commit
            # it so the failure is visible. No Celery retry in eager
            # mode. ConnectorOffline / CommandTimeout / TallyRetryableEnvelope
            # are retryable and the audit row reflects voucher.tally_post_queued
            # (Phase 0.5 re-enqueue infrastructure reads it). TallyRejectedEnvelope
            # and LedgerNotSyncedToTally are operator-action and reflect
            # voucher.tally_post_failed / voucher.tally_post_blocked.
            db.commit()
            if isinstance(exc, TallyRejectedEnvelope):
                logger.error(
                    "in-process dispatch failed for voucher %s: %s "
                    "(Tally rejected — operator action required)",
                    voucher_id,
                    exc,
                )
            elif isinstance(exc, LedgerNotSyncedToTally):
                logger.error(
                    "in-process dispatch blocked for voucher %s: %s "
                    "(ledger not synced to Tally — run sync_masters)",
                    voucher_id,
                    exc,
                )
            else:
                logger.warning(
                    "in-process dispatch deferred for voucher %s: %s "
                    "(no autoretry in eager mode)",
                    voucher_id,
                    exc,
                )
        except Exception:
            db.rollback()
            logger.exception(
                "in-process dispatch failed for voucher %s", voucher_id
            )
        finally:
            db.close()

    loop = asyncio.get_running_loop()
    loop.create_task(_drive())


# ---------------------------------------------------------------------
# Async dispatcher (called from the Celery task)
# ---------------------------------------------------------------------


async def dispatch_voucher_to_tally(
    *,
    db: Session,
    voucher_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
    registry: ConnectorRegistry | None = None,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    """Post one voucher to TallyPrime via the registered connector.

    Returns the connector's command_result payload on success. The
    DB row is updated in-place and committed by the caller.
    """
    registry = registry or _connector_registry_mod.get_registry()

    voucher = (
        db.query(Voucher)
        .filter(
            Voucher.id == voucher_id,
            Voucher.company_id == company_id,
        )
        .first()
    )
    if voucher is None:
        raise ValueError(
            f"voucher {voucher_id} not found in company {company_id}"
        )

    entries = (
        db.query(LedgerEntry)
        .filter(
            LedgerEntry.voucher_id == voucher_id,
            LedgerEntry.company_id == company_id,
        )
        .order_by(LedgerEntry.line_number)
        .all()
    )

    # Resolve ledger names for the post_voucher args. The connector
    # expects ledger NAMES (Tally is name-keyed), not our UUIDs.
    ledger_ids = [e.ledger_id for e in entries]
    ledger_names: dict[UUID, str] = dict(
        db.query(Ledger.id, Ledger.name)
        .filter(Ledger.id.in_(ledger_ids))
        .all()
    )
    # Pick the party name = the first Cr ledger for Receipt / first Dr
    # for Payment / etc. Phase-0 keeps it simple: the first non-bank
    # entry is the party. For tighter control the API can pass an
    # explicit party_name in the voucher row, but the model has none
    # in Phase 0.
    party_name = ledger_names.get(entries[0].ledger_id, "") if entries else ""

    args = {
        "voucher_type": voucher.voucher_type.value
        if hasattr(voucher.voucher_type, "value")
        else str(voucher.voucher_type),
        "date": voucher.date.isoformat(),
        "voucher_number": voucher.voucher_number or "",
        "party_name": party_name,
        "narration": voucher.narration or "",
        "as_optional": bool(voucher.is_optional_in_tally),
        "entries": [
            {
                "ledger_name": ledger_names.get(e.ledger_id, ""),
                "amount": str(e.amount),
                "entry_type": e.entry_type.value
                if hasattr(e.entry_type, "value")
                else str(e.entry_type),
            }
            for e in entries
        ],
    }

    audit = AuditEmitter(
        db,
        AuditContext(
            company=None,  # company resolved via _override below
            user=None,
            ip_address=None,
            user_agent="celery-worker/1.0",
            request_id=request_id,
            source="worker",
        ),
    )

    # BUG-005 step 3: defense-in-depth guard. Catches any voucher that
    # reached the dispatcher with unsynced ledgers — either bypassing
    # the API-layer guard (non-API call path) or whose ledgers became
    # unsynced between API check and dispatch. Pre-flight reject: does
    # NOT increment tally_post_attempts (this is not a Tally attempt);
    # status stays at pending_tally_post so the eventual re-dispatch
    # (after sync_masters runs) needs no state transition.
    if not get_settings().TAXMIND_SKIP_TALLY_DISPATCH:
        try:
            check_ledgers_synced(
                db, ledger_ids=ledger_ids, company_id=company_id
            )
        except LedgerNotSyncedToTally as exc:
            voucher.tally_last_error = exc.message
            audit.emit(
                action="voucher.tally_post_blocked",
                entity_type="voucher",
                entity_id=voucher.id,
                old_value=None,
                new_value={
                    "code": exc.code,
                    "message": exc.message,
                    **(exc.details or {}),
                },
                actor_user_id=user_id,
                company_id_override=company_id,
            )
            raise

    try:
        result = await registry.send_command(
            company_id=company_id,
            command="post_voucher",
            args=args,
            timeout_seconds=timeout_seconds,
            idempotency_key=str(voucher_id),
        )
    except (ConnectorOffline, CommandTimeout) as exc:
        voucher.tally_post_attempts = (
            voucher.tally_post_attempts or 0
        ) + 1
        voucher.tally_last_error = str(exc)
        # P0.46d: retryable failures keep the voucher in
        # `pending_tally_post` and emit `voucher.tally_post_queued`
        # (the queue-on-mismatch signal from v1.3 AUDIT.md). Non-
        # retryable connector errors stay on `voucher.tally_post_failed`
        # below — they're the ones a human has to inspect.
        audit.emit(
            action="voucher.tally_post_queued",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value={
                "error": str(exc),
                "error_class": exc.__class__.__name__,
                "retry_attempt": voucher.tally_post_attempts,
            },
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        raise

    if result.get("status") != "success":
        # BUG-Books-004 Layer A/B fix: the connector now distinguishes
        # retryable (transport / ambiguous response) from non-retryable
        # (Tally rejected for operator-fixable reason) via the envelope's
        # `retryable` field. The dispatcher reads that field and routes
        # to the correct audit action + raises the matching exception so
        # _drive (eager mode) or the Celery worker (future) handles each
        # appropriately.
        error_dict = result.get("error") or {}
        error_code = (
            error_dict.get("code", "unknown_error")
            if isinstance(error_dict, dict)
            else "unknown_error"
        )
        error_message = (
            error_dict.get("message", "unknown error")
            if isinstance(error_dict, dict)
            else str(error_dict) or "unknown error"
        )
        voucher.tally_post_attempts = (
            voucher.tally_post_attempts or 0
        ) + 1
        voucher.tally_last_error = error_message
        if result.get("retryable") is True:
            audit.emit(
                action="voucher.tally_post_queued",
                entity_type="voucher",
                entity_id=voucher.id,
                old_value=None,
                new_value={
                    "error": error_dict,
                    "error_class": error_code,
                    "retry_attempt": voucher.tally_post_attempts,
                },
                actor_user_id=user_id,
                company_id_override=company_id,
            )
            raise TallyRetryableEnvelope(error_code, error_message)
        audit.emit(
            action="voucher.tally_post_failed",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value={
                "error": error_dict,
                "error_class": error_code,
                "retry_attempt": voucher.tally_post_attempts,
            },
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        raise TallyRejectedEnvelope(error_code, error_message)

    voucher.tally_posted_at = datetime.now(UTC)
    voucher.tally_voucher_guid = (
        result.get("result", {}).get("tally_voucher_guid")
    )
    voucher.tally_last_error = None
    # P0.46d: dispatcher is the only place that flips status to
    # `posted`. Idempotent — vouchers seeded directly as `posted`
    # (Optional flow re-approval, tests, backfills) stay that way.
    if voucher.status == VoucherStatus.pending_tally_post:
        voucher.status = VoucherStatus.posted
    posted_as_optional = bool(voucher.is_optional_in_tally)
    audit.emit(
        action=(
            "voucher.posted_as_optional"
            if posted_as_optional
            else "voucher.posted_to_tally"
        ),
        entity_type="voucher",
        entity_id=voucher.id,
        old_value=None,
        new_value={
            "tally_voucher_guid": voucher.tally_voucher_guid,
            "duration_ms": result.get("duration_ms"),
            "as_optional": posted_as_optional,
        },
        actor_user_id=user_id,
        company_id_override=company_id,
    )
    return result
