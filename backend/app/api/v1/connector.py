"""Connector endpoints (P0.23: enroll + issue code; P0.25: status)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_active_company, get_current_user, require_role
from app.api.v1.auth import _system_audit_emitter, _user_audit_emitter
from app.core.database import get_db
from app.core.security import CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS
from app.models.company import Company, CompanyRole
from app.models.user import User
from app.schemas.connector import (
    ConnectorStatusOut,
    EnrollmentCodeOut,
    EnrollRequest,
    EnrollResponse,
)
from app.services.connector_service import (
    _CodeAlreadyConsumed,
    _CodeExpired,
    _CodeNotFound,
    ConnectorEnrollmentService,
)
from app.services.tally.connector_registry import get_registry

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
    snap = get_registry().status_for(company.id)
    if snap is None:
        return ConnectorStatusOut(
            company_id=company.id, connected=False
        )
    return ConnectorStatusOut(**snap)
