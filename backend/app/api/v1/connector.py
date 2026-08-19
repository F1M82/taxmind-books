"""Connector endpoints (P0.23 enroll/code; P0.25 status; P0.27 sync;
P0.46b sync_masters → ledger ingest)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response, status
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
from app.models.company import Company, CompanyRole, UserCompany
from app.models.connector import Connector, ConnectorCompanyBinding, TallyCompanyDiscovery
from app.models.user import User
from app.schemas.connector import (
    CompanyMappingConfirmRequest,
    CompanyMappingConfirmResponse,
    CompanyMappingStatusOut,
    ConnectorStatusOut,
    EnrollmentCodeOut,
    EnrollRequest,
    EnrollResponse,
    SyncTriggerResponse,
)
from app.schemas.tally_mapping import (
    ActiveTallyCompanyOut,
    TallyCompaniesOut,
    TallyCompanyDiscoveryOut,
    TallyMappingOut,
    TallyMappingRequest,
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
from app.services.tally.company_mapping import (
    confirm_company_mapping,
    require_safe_company_mapping,
)
from app.services.tally.discovery_service import bind_discovery_reference, ingest_discovery

logger = logging.getLogger("app.api.v1.connector")

router = APIRouter(prefix="/connector", tags=["connector"])


def _connector_authorized(db: Session, user: User, connector_id: UUID) -> bool:
    """A connector is visible only through an owner/admin served company."""
    return db.query(UserCompany).join(
        ConnectorCompanyBinding, ConnectorCompanyBinding.company_id == UserCompany.company_id
    ).filter(
        ConnectorCompanyBinding.connector_id == connector_id,
        UserCompany.user_id == user.id,
        UserCompany.role.in_([CompanyRole.owner, CompanyRole.admin]),
    ).first() is not None or db.query(UserCompany).join(
        Connector, Connector.enrolled_company_id == UserCompany.company_id
    ).filter(
        Connector.id == connector_id, UserCompany.user_id == user.id,
        UserCompany.role.in_([CompanyRole.owner, CompanyRole.admin]),
    ).first() is not None


def _connector_in_context(
    db: Session, user: User, company: Company, connector_id: UUID, *, admin: bool = False
) -> Connector:
    connector = db.query(Connector).filter(Connector.id == connector_id).first()
    if connector is None:
        from app.core.exceptions import NotFound
        raise NotFound("Connector not found.")
    binding = db.query(ConnectorCompanyBinding).filter(
        ConnectorCompanyBinding.connector_id == connector_id,
        ConnectorCompanyBinding.company_id == company.id,
    ).first()
    if connector.enrolled_company_id != company.id and binding is None:
        from app.core.exceptions import Forbidden
        raise Forbidden("Connector is not authorized for this company.")
    if admin and not _connector_authorized(db, user, connector_id):
        from app.core.exceptions import Forbidden
        raise Forbidden("Connector is not authorized for this user.")
    return connector


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
    db: Session = Depends(get_db),
) -> ConnectorStatusOut:
    snap = _connector_registry_mod.get_registry().status_for(company.id)
    if snap is None:
        connector = db.query(Connector.id).filter(
            Connector.enrolled_company_id == company.id
        ).order_by(Connector.created_at.desc()).first()
        if connector is None:
            connector = db.query(ConnectorCompanyBinding.connector_id).filter(
                ConnectorCompanyBinding.company_id == company.id
            ).order_by(ConnectorCompanyBinding.configured_at.desc()).first()
        return ConnectorStatusOut(
            company_id=company.id, connector_id=connector[0] if connector else None,
            connected=False
        )
    return ConnectorStatusOut(**snap)


@router.get("/{connector_id}/tally-companies", response_model=TallyCompaniesOut)
async def tally_companies(
    connector_id: UUID,
    request: Request,
    refresh: bool = Query(False),
    user: User = Depends(get_current_user),
    company: Company = Depends(get_active_company),
    db: Session = Depends(get_db),
) -> TallyCompaniesOut:
    connector = _connector_in_context(db, user, company, connector_id)
    if refresh:
        registry = _connector_registry_mod.get_registry()
        if not registry.is_online(connector_id=connector_id):
            from app.core.exceptions import ConnectorOffline
            raise ConnectorOffline("Connector is not connected.")
        result = await registry.send_command(connector_id=connector_id, company_id=company.id, command="list_tally_companies", args={})
        if result.get("status") != "success":
            from app.core.exceptions import ConnectorOffline
            raise ConnectorOffline("Connector failed to discover Tally companies.")
        payload = result.get("result")
        if not isinstance(payload, dict):
            payload = result if isinstance(result, dict) else {}
        audit = _user_audit_emitter(request, db, user, company=company)
        ingest_discovery(db, connector_id=connector_id,
            data_folder_path=str(payload.get("tally_data_folder_path") or ""),
            companies=payload.get("companies") or [], audit=audit)
        db.commit()
    rows = db.query(TallyCompanyDiscovery).filter(TallyCompanyDiscovery.connector_id == connector_id).order_by(TallyCompanyDiscovery.tally_company_identifier).all()
    member_company_ids = {
        row[0]
        for row in db.query(UserCompany.company_id).filter(
            UserCompany.user_id == user.id
        ).all()
    }
    member_role = db.query(UserCompany.role).filter(
        UserCompany.user_id == user.id,
        UserCompany.company_id == company.id,
    ).scalar()
    show_diagnostics = member_role in {CompanyRole.owner, CompanyRole.admin}
    mapped = {
        r.tally_company_identifier: r.company_id
        for r in db.query(ConnectorCompanyBinding).filter(
            ConnectorCompanyBinding.connector_id == connector_id,
            ConnectorCompanyBinding.company_id.in_(member_company_ids),
        ).all()
    }
    return TallyCompaniesOut(connector_id=connector_id,
        tally_data_folder_path=connector.data_folder_path if show_diagnostics else None,
        scanned_at=max((r.scanned_at for r in rows), default=None), companies=[TallyCompanyDiscoveryOut(
            discovery_id=r.id, tally_company_identifier=r.tally_company_identifier, tally_company_name=r.tally_company_name,
            tally_master_id=r.tally_master_id if show_diagnostics else None,
            gstin=r.gstin, financial_year_start=r.financial_year_start,
            mapped_to_backend_company_id=mapped.get(r.tally_company_identifier)) for r in rows])


@router.post("/tally-mapping", response_model=TallyMappingOut)
def configure_discovery_mapping(
    body: TallyMappingRequest,
    request: Request,
    company: Company = Depends(require_role(CompanyRole.owner, CompanyRole.admin)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TallyMappingOut:
    discovery = db.query(TallyCompanyDiscovery).filter(TallyCompanyDiscovery.id == body.discovery_id).first()
    if discovery is None:
        from app.core.exceptions import NotFound
        raise NotFound("Tally company was not found in discovery.")
    _connector_in_context(db, user, company, discovery.connector_id, admin=True)
    audit = _user_audit_emitter(request, db, user, company=company)
    binding = bind_discovery_reference(db, company=company, discovery_id=body.discovery_id,
        user_id=user.id, audit=audit)
    db.commit()
    db.refresh(binding)
    return TallyMappingOut(company_id=company.id, connector_id=binding.connector_id,
        tally_data_folder_path=binding.data_folder_path, tally_company_identifier=binding.tally_company_identifier,
        tally_company_display_name=binding.tally_company_display_name,
        tally_mapping_configured_at=binding.configured_at, tally_mapping_configured_by=binding.configured_by)


@router.get("/{connector_id}/active-tally-company", response_model=ActiveTallyCompanyOut)
async def active_tally_company(
    connector_id: UUID,
    user: User = Depends(get_current_user),
    company: Company = Depends(get_active_company),
    db: Session = Depends(get_db),
) -> ActiveTallyCompanyOut:
    _connector_in_context(db, user, company, connector_id)
    registry = _connector_registry_mod.get_registry()
    if not registry.is_online(connector_id=connector_id):
        from app.core.exceptions import ConnectorOffline
        raise ConnectorOffline("Connector is not connected.")
    result = await registry.send_command(connector_id=connector_id, company_id=company.id,
        command="get_active_tally_company", args={})
    active = result.get("result")
    if not isinstance(active, dict):
        active = result if isinstance(result, dict) else {}
    member_role = db.query(UserCompany.role).filter(
        UserCompany.user_id == user.id,
        UserCompany.company_id == company.id,
    ).scalar()
    return ActiveTallyCompanyOut(connector_id=connector_id,
        tally_company_identifier=active.get("tally_company_identifier") or active.get("identifier"),
        tally_company_name=active.get("tally_company_name") or active.get("name"),
        tally_master_id=(active.get("tally_master_id") or active.get("tally_company_guid"))
        if member_role in {CompanyRole.owner, CompanyRole.admin} else None)


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
            company_info = payload.get("company") or {}
            ledgers_in = payload.get("ledgers") or []
            groups_in = payload.get("groups") or []
            try:
                counts = persist_sync_masters_payload(
                    company_id=actor_company_id,
                    user_id=actor_user_id,
                    request_id=task_id,
                    ledgers=ledgers_in,
                    groups=groups_in,
                    tally_company_guid=company_info.get("guid"),
                    tally_company_name=company_info.get("name"),
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
# Company Tally mapping (P3.7 Phase 7B)
# ---------------------------------------------------------------------


@router.get("/company-mapping", response_model=CompanyMappingStatusOut)
def company_mapping_status(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
) -> CompanyMappingStatusOut:
    """Read-only status of the active company's Tally company binding.

    Makes the unresolved state explicit: ``mapped=False`` + NULL
    ``tally_master_id`` means no identity proof exists yet, so the ledger
    persistence gate remains closed.
    """
    return CompanyMappingStatusOut(
        company_id=company.id,
        tally_master_id=company.tally_master_id,
        mapped=company.tally_master_id is not None,
    )


@router.post(
    "/company-mapping/confirm",
    response_model=CompanyMappingConfirmResponse,
)
def company_mapping_confirm(
    body: CompanyMappingConfirmRequest,
    request: Request,
    company: Company = Depends(require_role(CompanyRole.owner)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyMappingConfirmResponse:
    """Operator confirmation: bind the active company to a Tally company GUID.

    The operator explicitly selects the company (via X-Company-ID) AND
    supplies the Tally company GUID (from connector `company_info`); the GUID
    is never inferred from the name and never auto-selected. Confirmation is
    auditable (`company.tally_mapping_configured`). Conflicting GUID/name
    bindings raise 409 and leave the database unchanged.
    """
    audit = _user_audit_emitter(request, db, user, company=company)
    confirmed = confirm_company_mapping(
        db,
        company_id=company.id,
        tally_company_guid=body.tally_company_guid,
        tally_company_name=body.tally_company_name,
        audit=audit,
    )
    db.commit()
    db.refresh(confirmed)
    return CompanyMappingConfirmResponse(
        company_id=confirmed.id,
        tally_master_id=confirmed.tally_master_id or "",
        tally_company_name=body.tally_company_name,
    )


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
    tally_company_guid: str | None = None,
    tally_company_name: str | None = None,
) -> dict[str, int]:
    """Persist a successful `sync_masters` reply for `company_id`.

    Opens its own session (the API request session has already closed
    by the time the background task fires) and commits atomically.
    Loads the actor `Company` and `User` so the AuditEmitter writes
    rows with the correct tenant + actor attribution.

    Phase 7B fail-closed gate: the reply must carry the Tally company
    GUID and it must deterministically match ``company.tally_master_id``.
    If the company is unmapped or mapped to a *different* GUID, this
    raises ``CompanyMappingError`` before any ledger is written — no
    identity proof, no persistence.
    """
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        require_safe_company_mapping(
            db, company_id=company_id, tally_company_guid=tally_company_guid
        )
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
