"""Ledger dispatcher: backend → connector for `create_ledger` (v1.3 item 7).

Mirrors `voucher_dispatcher.py`'s shape exactly -- same eager/Celery split,
same retryable/rejection audit classification, same
`target_tally_company_identifier` wrong-company guard. A ledger pushed to
Tally is not retried on a schedule the way a voucher is (no 30-day expiry
sweep here): if the push fails, the ledger just stays
`created_via_mobile=True, confirmed_in_tally_at=None` indefinitely, which
is a safe default (every voucher referencing it keeps landing Optional
until an operator retries via `POST /ledgers/{id}/retry-tally-sync`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import AuditContext, AuditEmitter
from app.models.ledger import Ledger
from app.services.tally import connector_registry as _connector_registry_mod
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
    ConnectorRegistry,
    TallyRejectedEnvelope,
    TallyRetryableEnvelope,
)
from app.services.tally.voucher_dispatcher import (
    _resolve_target_company_identifier,
)

logger = logging.getLogger("app.services.tally.ledger_dispatcher")


# ---------------------------------------------------------------------
# Sync enqueue hook
# ---------------------------------------------------------------------


def enqueue_ledger_create(
    *,
    ledger_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
) -> None:
    """Fire-and-forget dispatch from the API request path.

    Same eager/Celery split as `voucher_dispatcher.enqueue_voucher_post` --
    see that function's docstring for why eager mode schedules an
    `asyncio.create_task` rather than going through Celery.
    """
    settings = get_settings()

    if settings.TAXMIND_SKIP_TALLY_DISPATCH:
        return

    if settings.CELERY_TASK_ALWAYS_EAGER:
        _enqueue_in_process(
            ledger_id=ledger_id,
            company_id=company_id,
            user_id=user_id,
            request_id=request_id,
        )
        return

    from app.workers.posting_tasks import create_ledger_in_tally

    create_ledger_in_tally.delay(
        ledger_id=str(ledger_id),
        company_id=str(company_id),
        user_id=str(user_id) if user_id else None,
        request_id=str(request_id),
    )


def _enqueue_in_process(
    *,
    ledger_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
) -> None:
    import asyncio

    from app.core.database import SessionLocal

    async def _drive() -> None:
        db = SessionLocal()
        try:
            await dispatch_ledger_to_tally(
                db=db,
                ledger_id=ledger_id,
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
        ) as exc:
            db.commit()
            logger.warning(
                "in-process ledger dispatch deferred for %s: %s",
                ledger_id,
                exc,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "in-process ledger dispatch failed for %s", ledger_id
            )
        finally:
            db.close()

    loop = asyncio.get_running_loop()
    loop.create_task(_drive())


# ---------------------------------------------------------------------
# Async dispatcher (called from the Celery task)
# ---------------------------------------------------------------------


async def dispatch_ledger_to_tally(
    *,
    db: Session,
    ledger_id: UUID,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
    registry: ConnectorRegistry | None = None,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    """Push one ledger to TallyPrime via the registered connector.

    On success, stamps `tally_master_id` (Tally's own GUID, read back by
    name after the Create -- live-verified 2026-10-07 that Tally does
    NOT honor REMOTEID on a LEDGER Create the way it does on VOUCHER, so
    the connector confirms the real GUID itself rather than this backend
    assuming the id it sent was accepted) + `tally_synced_at` +
    `confirmed_in_tally_at`, and audits `ledger.confirmed_in_tally`. A
    voucher-create-time check reads `confirmed_in_tally_at` to decide
    whether to still force Optional -- see `voucher_service.py`.
    """
    registry = registry or _connector_registry_mod.get_registry()

    ledger = (
        db.query(Ledger)
        .filter(Ledger.id == ledger_id, Ledger.company_id == company_id)
        .first()
    )
    if ledger is None:
        raise ValueError(
            f"ledger {ledger_id} not found in company {company_id}"
        )

    args: dict[str, object] = {
        "ledger_id": str(ledger.id),
        "name": ledger.name,
        "group_name": ledger.group_name or "",
        "opening_balance": str(ledger.opening_balance),
        "balance_type": ledger.balance_type.value
        if hasattr(ledger.balance_type, "value")
        else str(ledger.balance_type),
    }

    target_identifier = _resolve_target_company_identifier(
        db, registry=registry, company_id=company_id
    )
    if target_identifier is not None:
        args["target_tally_company_identifier"] = target_identifier

    audit = AuditEmitter(
        db,
        AuditContext(
            company=None,
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
            command="create_ledger",
            args=args,
            timeout_seconds=timeout_seconds,
            idempotency_key=str(ledger_id),
        )
    except (ConnectorOffline, CommandTimeout) as exc:
        audit.emit(
            action="ledger.sync_failed",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=None,
            new_value={
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            },
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        raise

    if result.get("status") != "success":
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
        audit.emit(
            action="ledger.sync_failed",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=None,
            new_value={"error": error_dict, "error_class": error_code},
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        if result.get("retryable") is True:
            raise TallyRetryableEnvelope(error_code, error_message)
        raise TallyRejectedEnvelope(error_code, error_message)

    master_id = result.get("tally_master_id")
    if not master_id:
        # Contract violation: a "success" result must carry Tally's real
        # GUID (the connector confirms it by read-back; see
        # TallyClient.create_ledger). Never fall back to a made-up value
        # here -- that's exactly the identity-corruption bug this dance
        # exists to avoid. Treat as ambiguous/retryable instead.
        audit.emit(
            action="ledger.sync_failed",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=None,
            new_value={
                "error": "success result missing tally_master_id",
                "error_class": "TallyAmbiguousResponse",
            },
            actor_user_id=user_id,
            company_id_override=company_id,
        )
        raise TallyRetryableEnvelope(
            "missing_tally_master_id",
            "Tally create succeeded but returned no confirmed master id",
        )

    now = datetime.now(UTC)
    ledger.tally_master_id = master_id
    ledger.tally_synced_at = now
    ledger.confirmed_in_tally_at = now
    audit.emit(
        action="ledger.confirmed_in_tally",
        entity_type="ledger",
        entity_id=ledger.id,
        old_value=None,
        new_value={
            "tally_master_id": ledger.tally_master_id,
            "duration_ms": result.get("duration_ms"),
        },
        actor_user_id=user_id,
        company_id_override=company_id,
    )
    return result
