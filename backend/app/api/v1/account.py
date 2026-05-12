"""Account-lifecycle endpoints (P0.45).

POST   /api/v1/account/deletion-request — open a 30-day grace window.
DELETE /api/v1/account/deletion-request — cancel during grace.

User-scoped (no X-Company-ID). Sole-owner protection lives in the
service layer; this module is purely transport.

Phase 0 ships deletion-request only; data-export endpoints (also
under `/account/*`) are Phase 1.
"""

from __future__ import annotations

import ipaddress
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.audit import AuditContext, AuditEmitter
from app.core.database import get_db
from app.models.user import User
from app.schemas.account import (
    AccountDeletionCancelResponse,
    AccountDeletionCreateRequest,
    AccountDeletionResponse,
)
from app.services import account_lifecycle_service

router = APIRouter(prefix="/account", tags=["account"])


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


def _audit_emitter(
    request: Request, db: Session, user: User
) -> AuditEmitter:
    return AuditEmitter(
        db,
        AuditContext(
            # Account lifecycle is user-scoped, not company-scoped.
            company=None,
            user=user,
            ip_address=_coerce_ip(
                request.client.host if request.client else None
            ),
            user_agent=request.headers.get("user-agent"),
            request_id=_resolve_request_id(
                request.headers.get("X-Request-ID")
            ),
            source="api",
        ),
    )


@router.post(
    "/deletion-request",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountDeletionResponse,
)
def create_deletion_request(
    _data: AccountDeletionCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountDeletionResponse:
    audit = _audit_emitter(request, db, user)
    row = account_lifecycle_service.request_deletion(
        db, audit, user=user
    )
    db.commit()
    return AccountDeletionResponse(
        id=row.id,
        status=row.status.value,
        requested_at=row.requested_at,
        grace_ends_at=row.grace_ends_at,
    )


@router.delete(
    "/deletion-request",
    status_code=status.HTTP_200_OK,
    response_model=AccountDeletionCancelResponse,
)
def cancel_deletion_request(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountDeletionCancelResponse:
    audit = _audit_emitter(request, db, user)
    row = account_lifecycle_service.cancel_deletion(
        db, audit, user=user
    )
    db.commit()
    assert row.cancelled_at is not None  # set by the service
    return AccountDeletionCancelResponse(
        status=row.status.value,
        cancelled_at=row.cancelled_at,
    )
