"""Ledgers endpoints (P0.17). All require auth + X-Company-ID."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_scoped_session,
)
from app.api.v1.auth import _user_audit_emitter
from app.core.audit import AuditEmitter
from app.models.company import Company
from app.models.user import User
from app.schemas.ledger import (
    LedgerCreate,
    LedgerListItem,
    LedgerListResponse,
    LedgerOut,
    LedgerUpdate,
)
from app.services.ledger_service import LedgerService

router = APIRouter(prefix="/ledgers", tags=["ledgers"])


def _audit(
    request: Request, db: Session, user: User, company: Company
) -> AuditEmitter:
    return _user_audit_emitter(request, db, user, company=company)


def _to_out(led) -> LedgerOut:  # type: ignore[no-untyped-def]
    return LedgerOut(
        id=led.id,
        company_id=led.company_id,
        name=led.name,
        name_normalized=led.name_normalized,
        group_name=led.group_name,
        parent_ledger_id=led.parent_ledger_id,
        opening_balance=led.opening_balance,
        balance_type=led.balance_type.value
        if hasattr(led.balance_type, "value")
        else str(led.balance_type),
        gstin=led.gstin,
        pan=led.pan,
        phone=led.phone,
        email=led.email,
        address=led.address,
        state_code=led.state_code,
        is_active=led.is_active,
        tally_master_id=led.tally_master_id,
        tally_synced_at=led.tally_synced_at,
        created_at=led.created_at,
        updated_at=led.updated_at,
    )


# ---------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------


@router.post(
    "/", status_code=status.HTTP_201_CREATED, response_model=LedgerOut
)
def create_ledger(
    data: LedgerCreate,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> LedgerOut:
    audit = _audit(request, db, user, company)
    service = LedgerService(db, audit, company_id=company.id)
    ledger = service.create(data)
    db.commit()
    db.refresh(ledger)
    return _to_out(ledger)


# ---------------------------------------------------------------------
# List + fuzzy search
# ---------------------------------------------------------------------


@router.get("/", response_model=LedgerListResponse)
def list_ledgers(
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    group: str | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LedgerListResponse:
    audit = _audit(request, db, user, company)
    service = LedgerService(db, audit, company_id=company.id)
    rows, total = service.list(
        group=group, is_active=is_active, q=q, limit=limit
    )
    return LedgerListResponse(
        items=[
            LedgerListItem(
                id=r.id,
                name=r.name,
                group_name=r.group_name,
                opening_balance=r.opening_balance,
                balance_type=r.balance_type.value
                if hasattr(r.balance_type, "value")
                else str(r.balance_type),
                gstin=r.gstin,
                is_active=r.is_active,
            )
            for r in rows
        ],
        meta={"next_cursor": None, "total": total},
    )


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------


@router.get("/{ledger_id}", response_model=LedgerOut)
def get_ledger(
    ledger_id: UUID,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> LedgerOut:
    audit = _audit(request, db, user, company)
    service = LedgerService(db, audit, company_id=company.id)
    return _to_out(service.get(ledger_id))


# ---------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------


@router.patch("/{ledger_id}", response_model=LedgerOut)
def update_ledger(
    ledger_id: UUID,
    data: LedgerUpdate,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> LedgerOut:
    audit = _audit(request, db, user, company)
    service = LedgerService(db, audit, company_id=company.id)
    ledger = service.update(ledger_id, data)
    db.commit()
    db.refresh(ledger)
    return _to_out(ledger)


# ---------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------


@router.delete("/{ledger_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ledger(
    ledger_id: UUID,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> Response:
    audit = _audit(request, db, user, company)
    service = LedgerService(db, audit, company_id=company.id)
    service.soft_delete(ledger_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
