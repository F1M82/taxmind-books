"""Companies endpoints (P0.16)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.api.v1.auth import _user_audit_emitter
from app.core.audit import AuditEmitter
from app.core.database import get_db
from app.models.company import Company, CompanyRole, UserCompany
from app.models.connector import Connector, ConnectorCompanyBinding, TallyCompanyDiscovery
from app.models.user import User
from app.schemas.company import (
    CompanyCreate,
    CompanyListItem,
    CompanyListResponse,
    CompanyOut,
    CompanyUpdate,
    MemberAddRequest,
    MemberListResponse,
    MemberOut,
    MemberRoleUpdate,
    PaginationMeta,
)
from app.schemas.tally_mapping import TallyMappingOut, TallyMappingRequest
from app.services.company_service import CompanyService
from app.services.tally.discovery_service import bind_discovery_reference

router = APIRouter(prefix="/companies", tags=["companies"])


@router.post("/{company_id}/tally-mapping", response_model=TallyMappingOut)
def configure_tally_mapping(
    company_id: UUID,
    body: TallyMappingRequest,
    request: Request,
    company: Company = Depends(require_role(CompanyRole.owner, CompanyRole.admin)),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TallyMappingOut:
    if company_id != company.id:
        from app.core.exceptions import CompanyNotFound
        raise CompanyNotFound("Company not found.")
    discovery = db.query(TallyCompanyDiscovery).filter(TallyCompanyDiscovery.id == body.discovery_id).first()
    if discovery is None:
        from app.core.exceptions import NotFound
        raise NotFound("Tally company was not found in discovery.")
    connector = db.query(Connector).filter(Connector.id == discovery.connector_id).first()
    authorized = db.query(UserCompany).filter(
        UserCompany.user_id == user.id,
        UserCompany.company_id == connector.enrolled_company_id,
        UserCompany.role.in_([CompanyRole.owner, CompanyRole.admin]),
    ).first()
    if authorized is None:
        authorized = db.query(UserCompany).join(
            ConnectorCompanyBinding,
            ConnectorCompanyBinding.company_id == UserCompany.company_id,
        ).filter(
            ConnectorCompanyBinding.connector_id == discovery.connector_id,
            UserCompany.user_id == user.id,
            UserCompany.role.in_([CompanyRole.owner, CompanyRole.admin]),
        ).first()
    if connector is None or authorized is None:
        from app.core.exceptions import Forbidden
        raise Forbidden("Connector is not authorized for this company.")
    audit = _user_audit_emitter(request, db, user, company=company)
    binding = bind_discovery_reference(db, company=company, discovery_id=body.discovery_id,
        user_id=user.id, audit=audit)
    db.commit()
    db.refresh(binding)
    return TallyMappingOut(company_id=company.id, connector_id=binding.connector_id,
        tally_data_folder_path=binding.data_folder_path, tally_company_identifier=binding.tally_company_identifier,
        tally_company_display_name=binding.tally_company_display_name,
        tally_mapping_configured_at=binding.configured_at, tally_mapping_configured_by=binding.configured_by)


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
    return _member_out(membership)


def _member_out(membership) -> MemberOut:  # type: ignore[no-untyped-def]
    return MemberOut(
        id=membership.id,
        user_id=membership.user_id,
        company_id=membership.company_id,
        role=membership.role.value,
        user_email=membership.user.email,
        created_at=membership.created_at,
    )


@router.get("/{company_id}/members", response_model=MemberListResponse)
def list_members(
    company_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberListResponse:
    service = CompanyService(db, _audit(request, db, user))
    members = service.list_members(company_id, actor=user)
    return MemberListResponse(items=[_member_out(m) for m in members])


@router.patch(
    "/{company_id}/members/{user_id}", response_model=MemberOut
)
def update_member_role(
    company_id: UUID,
    user_id: UUID,
    data: MemberRoleUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    service = CompanyService(db, _audit(request, db, user))
    membership = service.set_member_role(
        company_id, user_id, data.role, actor=user
    )
    db.commit()
    db.refresh(membership)
    db.refresh(membership.user)
    return _member_out(membership)


@router.delete(
    "/{company_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_member(
    company_id: UUID,
    user_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    service = CompanyService(db, _audit(request, db, user))
    service.remove_member(company_id, user_id, actor=user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
