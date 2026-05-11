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
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.audit import AuditContext, AuditEmitter
from app.models.company import Company
from app.models.user import User
from app.models.voucher import LedgerEntry, Voucher
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
    ConnectorRegistry,
    get_registry,
)

logger = logging.getLogger("app.services.tally.voucher_dispatcher")


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
    """Fire-and-forget Celery dispatch from the API request path.

    Honors `TAXMIND_SKIP_TALLY_DISPATCH=1` so the test suite can run
    without a live Redis broker.

    Deferred import so a test-time monkey-patch on
    `app.workers.posting_tasks.post_voucher_to_tally` flows through.
    """
    import os

    if os.environ.get("TAXMIND_SKIP_TALLY_DISPATCH") == "1":
        return

    from app.workers.posting_tasks import post_voucher_to_tally

    post_voucher_to_tally.delay(
        voucher_id=str(voucher_id),
        company_id=str(company_id),
        user_id=str(user_id) if user_id else None,
        request_id=str(request_id),
    )


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
    registry = registry or get_registry()

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
    from app.models.ledger import Ledger

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
        audit.emit(
            action="voucher.tally_post_failed",
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
        voucher.tally_post_attempts = (
            voucher.tally_post_attempts or 0
        ) + 1
        voucher.tally_last_error = str(result.get("error", "unknown error"))
        audit.emit(
            action="voucher.tally_post_failed",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value={
                "error": result.get("error"),
                "retry_attempt": voucher.tally_post_attempts,
            },
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        return result

    voucher.tally_posted_at = datetime.now(UTC)
    voucher.tally_voucher_guid = (
        result.get("result", {}).get("tally_voucher_guid")
    )
    voucher.tally_last_error = None
    audit.emit(
        action="voucher.posted_to_tally",
        entity_type="voucher",
        entity_id=voucher.id,
        old_value=None,
        new_value={
            "tally_voucher_guid": voucher.tally_voucher_guid,
            "duration_ms": result.get("duration_ms"),
        },
        actor_user_id=user_id,
        company_id_override=company_id,
    )
    return result
