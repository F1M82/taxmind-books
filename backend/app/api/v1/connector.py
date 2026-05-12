"""Connector endpoints (P0.23 enroll/code; P0.25 status; P0.27 sync)."""

from __future__ import annotations

import asyncio
import logging
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
from app.core.database import get_db
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
    Returns 202 immediately; the actual ledger/group ingestion is
    handled by P0.26's connector command flow (the result fan-out
    lands in P1+).
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

    # Spawn the actual sync_masters dispatch on a background task.
    # The reply is logged but not propagated back to this HTTP
    # response (202 means "accepted, not done"); future endpoints
    # surface ingestion state via the audit log + status snapshot.
    task_id = uuid4()

    async def _drive() -> None:
        try:
            result = await registry.send_command(
                company_id=company.id,
                command="sync_masters",
                args={},
                timeout_seconds=120,  # full pull can be slow
                idempotency_key=str(task_id),
            )
            logger.info(
                "sync_masters %s for %s returned status=%s",
                task_id,
                company.id,
                result.get("status"),
            )
        except Exception:
            logger.exception(
                "sync_masters %s failed for %s", task_id, company.id
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
