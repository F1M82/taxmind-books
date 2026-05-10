"""Auth endpoints: register, login, refresh, me, password."""

from __future__ import annotations

import ipaddress
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.core.audit import AuditContext, AuditEmitter
from app.core.database import get_db
from app.models.company import UserCompany
from app.models.user import User
from app.schemas.auth import (
    CompanyMembershipOut,
    MeResponse,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    TokenUserOut,
    UserOut,
)
from app.services.auth_service import AuthService, issue_token_pair

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------
# Audit-context helpers
# ---------------------------------------------------------------------


def _coerce_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return None
    return raw


def _resolve_request_id(raw: str | None) -> UUID:
    if not raw:
        return uuid4()
    try:
        return UUID(raw)
    except ValueError:
        return uuid4()


def _build_audit_ctx(
    request: Request,
    *,
    user: User | None,
    company=None,  # type: ignore[no-untyped-def]
) -> AuditContext:
    return AuditContext(
        company=company,
        user=user,
        ip_address=_coerce_ip(
            request.client.host if request.client else None
        ),
        user_agent=request.headers.get("user-agent"),
        request_id=_resolve_request_id(request.headers.get("X-Request-ID")),
        source="api",
    )


def _system_audit_emitter(request: Request, db: Session) -> AuditEmitter:
    return AuditEmitter(db, _build_audit_ctx(request, user=None))


def _user_audit_emitter(
    request: Request,
    db: Session,
    user: User,
    company=None,  # type: ignore[no-untyped-def]
) -> AuditEmitter:
    return AuditEmitter(db, _build_audit_ctx(request, user=user, company=company))


# ---------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserOut,
)
def register(
    data: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UserOut:
    audit = _system_audit_emitter(request, db)
    service = AuthService(db, audit)
    user = service.register(data)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------


def _token_response(user: User) -> TokenResponse:
    cfg = get_settings()
    access, refresh = issue_token_pair(user)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=cfg.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=TokenUserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """OAuth2 password-flow login. Form-encoded `username` + `password`.

    `username` is treated as the user's email (case-insensitive).
    """
    audit = _system_audit_emitter(request, db)
    service = AuthService(db, audit)
    user = service.authenticate(form.username, form.password)
    db.commit()  # persist last_login_at
    db.refresh(user)
    return _token_response(user)


# ---------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    audit = _system_audit_emitter(request, db)
    service = AuthService(db, audit)
    user = service.refresh(body.refresh_token)
    return _token_response(user)


# ---------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Return the authenticated user + their company memberships."""
    rows = (
        db.query(UserCompany)
        .filter(UserCompany.user_id == user.id)
        .all()
    )
    companies: list[CompanyMembershipOut] = [
        CompanyMembershipOut(
            id=m.company.id,
            name=m.company.name,
            role=m.role.value,
        )
        for m in rows
    ]
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_ca=user.is_ca,
        firm_name=user.firm_name,
        is_active=user.is_active,
        companies=companies,
    )


# ---------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    data: PasswordChangeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    audit = _user_audit_emitter(request, db, user)
    service = AuthService(db, audit)
    service.change_password(user, data.current_password, data.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
