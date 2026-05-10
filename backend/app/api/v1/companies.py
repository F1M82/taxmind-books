"""Companies endpoints (P0.16)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.v1.auth import _user_audit_emitter
from app.core.audit import AuditEmitter
from app.core.database import get_db
from app.models.user import User
from app.schemas.company import (
    CompanyCreate,
    CompanyListItem,
    CompanyListResponse,
    CompanyOut,
    CompanyUpdate,
    MemberAddRequest,
    MemberOut,
    PaginationMeta,
)
from app.services.company_service import CompanyService

router = APIRouter(prefix="/companies", tags=["companies"])


def _audit(
    request: Request, db: Session, user: User
) -> AuditEmitter:
    return _user_audit_emitter(request, db, user)


def _to_company_out(company, role: str) -> CompanyOut:  # type: ignore[no-untyped-def]
    return CompanyOut(
        id=company.id,
        name=company.name,
        gstin=company.gstin,
        pan=company.pan,
        financial_year_start=company.financial_year_start,
        status=company.status.value
        if hasattr(company.status, "value")
        else str(company.status),
        address=company.address,
        city=company.city,
        state_code=company.state_code,
        pincode=company.pincode,
        accounting_source=company.accounting_source,
        created_at=company.created_at,
        your_role=role,
    )


# ---------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=CompanyOut,
)
def create_company(
    data: CompanyCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyOut:
    audit = _audit(request, db, user)
    service = CompanyService(db, audit)
    company, _membership = service.create(data, actor=user)
    db.commit()
    db.refresh(company)
    return _to_company_out(company, role="owner")


# ---------------------------------------------------------------------
# List
# ---------------------------------------------------------------------


@router.get("/", response_model=CompanyListResponse)
def list_companies(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> CompanyListResponse:
    audit = _audit(request, db, user)
    service = CompanyService(db, audit)
    rows, next_cursor, total = service.list_for_user(
        user, limit=limit, cursor=cursor
    )
    return CompanyListResponse(
        items=[
            CompanyListItem(
                id=c.id,
                name=c.name,
                gstin=c.gstin,
                status=c.status.value
                if hasattr(c.status, "value")
                else str(c.status),
                your_role=role,
            )
            for (c, role) in rows
        ],
        meta=PaginationMeta(next_cursor=next_cursor, total=total),
    )


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(
    company_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyOut:
    audit = _audit(request, db, user)
    service = CompanyService(db, audit)
    company = service.get(company_id, actor=user)
    role = getattr(company, "_cached_role", "viewer")
    return _to_company_out(company, role=role)


# ---------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: UUID,
    data: CompanyUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompanyOut:
    audit = _audit(request, db, user)
    service = CompanyService(db, audit)
    company = service.update(company_id, data, actor=user)
    db.commit()
    db.refresh(company)
    role = getattr(company, "_cached_role", "viewer")
    return _to_company_out(company, role=role)


# ---------------------------------------------------------------------
# Add member
# ---------------------------------------------------------------------


@router.post(
    "/{company_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberOut,
)
def add_member(
    company_id: UUID,
    data: MemberAddRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    audit = _audit(request, db, user)
    service = CompanyService(db, audit)
    membership = service.add_member(
        company_id, data.email, data.role, actor=user
    )
    db.commit()
    db.refresh(membership)
    db.refresh(membership.user)
    return MemberOut(
        id=membership.id,
        user_id=membership.user_id,
        company_id=membership.company_id,
        role=membership.role.value,
        user_email=membership.user.email,
        created_at=membership.created_at,
    )
