"""Auth endpoints (P0.14: register only).

Login / refresh / me / password change land in P0.15.
"""

from __future__ import annotations

import ipaddress
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.audit import AuditContext, AuditEmitter
from app.core.database import get_db
from app.schemas.auth import RegisterRequest, UserOut
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _coerce_ip(raw: str | None) -> str | None:
    """Return `raw` only if it parses as an IPv4 / IPv6 address.

    Postgres `INET` rejects anything else, including the TestClient
    default `"testclient"` and any X-Forwarded-For misconfiguration.
    """
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


def _system_audit_emitter(request: Request, db: Session) -> AuditEmitter:
    """Build an AuditEmitter for an unauthenticated, no-tenant request.

    Used by /auth/register: the caller has no JWT and no active
    company, so the standard `get_audit_emitter` chain (which depends
    on `get_current_user` and `get_active_company`) cannot run.
    """
    ctx = AuditContext(
        company=None,
        user=None,
        ip_address=_coerce_ip(
            request.client.host if request.client else None
        ),
        user_agent=request.headers.get("user-agent"),
        request_id=_resolve_request_id(request.headers.get("X-Request-ID")),
        source="api",
    )
    return AuditEmitter(db, ctx)


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
    """Create a new user account. No auth required."""
    audit = _system_audit_emitter(request, db)
    service = AuthService(db, audit)
    user = service.register(data)
    db.commit()  # commits the user + audit row atomically
    db.refresh(user)
    return UserOut.model_validate(user)
