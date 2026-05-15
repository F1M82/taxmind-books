"""Connector endpoints (P0.23 enroll/code; P0.25 status; P0.27 sync;
P0.46b sync_masters → ledger ingest)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_idempotency_handler,
    get_scoped_session,
    require_role,
)
from app.api.v1.auth import _system_audit_emitter, _user_audit_emitter
from app.core.audit import AuditContext, AuditEmitter
from app.core.database import SessionLocal, get_db
from app.core.exceptions import ConnectorOffline as ConnectorOfflineHTTP
from app.core.idempotency import IdempotencyHandler
from app.core.security import CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS
from app.models.company import Company, CompanyRole
from app.models.user import User
from app.schemas.connector import (
    ConnectorStatusOut,
    EnrollmentCodeOut,
    EnrollRequest,
    EnrollResponse,
    SyncTriggerResponse,
)
from app.services.connector_service import (
    ConnectorEnrollmentService,
    _CodeAlreadyConsumed,
    _CodeExpired,
    _CodeNotFound,
)
from app.services.ledger_service import LedgerService

# Module import (not `from ... import get_registry`) keeps the lookup
# late so tests that monkeypatch connector_registry.get_registry see
# the patch. Convention applies to every patchable callable —
# registry, dispatcher, external clients. See CONNECTOR_PROTOCOL.md
# §"Patchable singletons".
from app.services.tally import connector_registry as _connector_registry_mod

logger = logging.getLogger("app.api.v1.connector")

router = APIRouter(prefix="/connector", tags=["connector"])


# ---------------------------------------------------------------------
# Issue: owner only, requires X-Company-ID
# ---------------------------------------------------------------------


@router.post(
    "/enrollment-codes",
    status_code=status.HTTP_201_CREATED,
    response_model=EnrollmentCodeOut,
)
def issue_enrollment_code(
    request: Request,
    company: Company = Depends(require_role(CompanyRole.owner)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentCodeOut:
    audit = _user_audit_emitter(request, db, user, company=company)
    service = ConnectorEnrollmentService(db, audit)
    row, raw_code = service.issue(company_id=company.id, created_by=user.id)
    db.commit()
    db.refresh(row)
    return EnrollmentCodeOut(
        code=raw_code,
        expires_at=row.expires_at,
        company_id=row.company_id,
    )


# ---------------------------------------------------------------------
# Enroll: no auth — code itself authenticates the connector
# ---------------------------------------------------------------------


@router.post("/enroll", response_model=EnrollResponse)
def enroll(
    body: EnrollRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> EnrollResponse:
    # System event audit (no user, no company on this unauth route).
    audit = _system_audit_emitter(request, db)
    service = ConnectorEnrollmentService(db, audit)
    try:
        connector_id, company_id, token = service.enroll(code=body.code)
    except (_CodeNotFound, _CodeExpired, _CodeAlreadyConsumed):
        raise
    db.commit()
    return EnrollResponse(
        connector_id=connector_id,
        company_id=company_id,
        connector_token=token,
        expires_in_days=CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS,
    )


# ---------------------------------------------------------------------
# Status: requires auth + X-Company-ID, any role
# ---------------------------------------------------------------------


@router.get("/status", response_model=ConnectorStatusOut)
def connector_status(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
) -> ConnectorStatusOut:
    snap = _connector_registry_mod.get_registry().status_for(company.id)
    if snap is None:
        return ConnectorStatusOut(
            company_id=company.id, connected=False
        )
    return ConnectorStatusOut(**snap)


# ---------------------------------------------------------------------
# Sync trigger
# ---------------------------------------------------------------------


@router.post(
    "/sync/{company_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
async def trigger_sync(
    company_id: UUID,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    idem: IdempotencyHandler = Depends(get_idempotency_handler),
) -> Response | SyncTriggerResponse:
    """Fire a `sync_masters` command at the active company's connector.

    Per API.md: requires auth, X-Company-ID, and Idempotency-Key.
    Returns 202 immediately; the connector reply is persisted in a
    background task (P0.46b) — successful payloads upsert into the
    `ledgers` table under `company.id` and emit per-row audit events;
    persistence failures emit `ledger.sync_failed` and surface in logs.
    """
    # The path `company_id` is a UX redundancy with X-Company-ID;
    # both must match (also a tenant-isolation safeguard).
    if company_id != company.id:
        raise ConnectorOfflineHTTP(
            "Path company_id does not match X-Company-ID header.",
        )

    replay = await idem.check(required=True)
    if replay is not None:
        return replay

    registry = _connector_registry_mod.get_registry()
    if not registry.is_online(company.id):
        raise ConnectorOfflineHTTP("Connector is not connected.")

    # Capture the actor + tenant ids *before* spawning the background
    # task. The request, company, and user objects are scoped to this
    # function; the bare ids carry through the task safely.
    task_id = uuid4()
    actor_company_id = company.id
    actor_user_id = user.id

    async def _drive() -> None:
        try:
            result = await registry.send_command(
                company_id=actor_company_id,
                command="sync_masters",
                args={},
                timeout_seconds=120,  # full pull can be slow
                idempotency_key=str(task_id),
            )
            status_str = result.get("status")
            logger.info(
                "sync_masters %s for %s returned status=%s",
                task_id,
                actor_company_id,
                status_str,
            )
            if status_str != "success":
                return
            payload = result.get("result") or {}
            ledgers_in = payload.get("ledgers") or []
            groups_in = payload.get("groups") or []
            try:
                counts = persist_sync_masters_payload(
                    company_id=actor_company_id,
                    user_id=actor_user_id,
                    request_id=task_id,
                    ledgers=ledgers_in,
                    groups=groups_in,
                )
                logger.info(
                    "sync_masters %s persisted for %s: "
                    "created=%d updated=%d skipped=%d",
                    task_id,
                    actor_company_id,
                    counts["created"],
                    counts["updated"],
                    counts["skipped"],
                )
            except Exception as exc:
                logger.exception(
                    "sync_masters %s persist failed for %s",
                    task_id,
                    actor_company_id,
                )
                _emit_sync_failure_audit(
                    company_id=actor_company_id,
                    user_id=actor_user_id,
                    request_id=task_id,
                    task_id=task_id,
                    error=exc,
                )
                raise
        except Exception:
            logger.exception(
                "sync_masters %s failed for %s", task_id, actor_company_id
            )

    asyncio.create_task(_drive())

    body = SyncTriggerResponse(
        task_id=task_id,
        status="sync_triggered",
        estimated_duration_seconds=30,
    )
    idem.store_response(
        status_code=202, body=body.model_dump(mode="json")
    )
    db.commit()
    return body


# ---------------------------------------------------------------------
# sync_masters payload persistence (P0.46b)
# ---------------------------------------------------------------------


def persist_sync_masters_payload(
    *,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
    ledgers: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, int]:
    """Persist a successful `sync_masters` reply for `company_id`.

    Opens its own session (the API request session has already closed
    by the time the background task fires) and commits atomically.
    Loads the actor `Company` and `User` so the AuditEmitter writes
    rows with the correct tenant + actor attribution.
    """
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        user = (
            db.query(User).filter(User.id == user_id).first()
            if user_id is not None
            else None
        )
        audit = AuditEmitter(
            db,
            AuditContext(
                company=company,
                user=user,
                ip_address=None,
                user_agent="connector-sync/1.0",
                request_id=request_id,
                source="connector",
            ),
        )
        service = LedgerService(db, audit, company_id=company_id)
        counts = service.upsert_from_sync(ledgers=ledgers, groups=groups)
        db.commit()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _emit_sync_failure_audit(
    *,
    company_id: UUID,
    user_id: UUID | None,
    request_id: UUID,
    task_id: UUID,
    error: BaseException,
) -> None:
    """Write a `ledger.sync_failed` audit row on its own fresh session.

    The persist session is already in a rolled-back state by the time
    we get here; we need an independent session so the failure record
    actually commits.
    """
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        user = (
            db.query(User).filter(User.id == user_id).first()
            if user_id is not None
            else None
        )
        AuditEmitter(
            db,
            AuditContext(
                company=company,
                user=user,
                ip_address=None,
                user_agent="connector-sync/1.0",
                request_id=request_id,
                source="connector",
            ),
        ).emit(
            action="ledger.sync_failed",
            entity_type="connector_sync",
            entity_id=task_id,
            old_value=None,
            new_value={
                "error": str(error),
                "error_class": error.__class__.__name__,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "failed to record ledger.sync_failed audit for %s", task_id
        )
    finally:
        db.close()
