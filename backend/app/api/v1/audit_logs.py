"""Audit-log read API (P0.20). owner / admin only.

Tenant scoping is applied explicitly here even though the auto-scoping
session would also do it for `TenantScopedMixin` models — `AuditLog`
intentionally is NOT a TenantScopedMixin subclass (system events
have NULL company_id; auto-scope would silently filter them out
under cross-cutting / admin queries planned for Phase 5+). For the
v1 tenant-scoped read API, we filter `WHERE company_id = active`
explicitly so system rows never leak.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_scoped_session,
    require_role,
)
from app.models.audit_log import AuditLog
from app.models.company import Company, CompanyRole
from app.models.user import User
from app.schemas.audit_log import AuditLogListResponse, AuditLogOut

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


@router.get(
    "/",
    response_model=AuditLogListResponse,
    dependencies=[
        Depends(require_role(CompanyRole.owner, CompanyRole.admin))
    ],
)
def list_audit_logs(
    company: Company = Depends(get_active_company),
    db: Session = Depends(get_scoped_session),
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    user_id: UUID | None = None,
    action: str | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditLogListResponse:
    q = (
        db.query(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.user_id)
        .filter(AuditLog.company_id == company.id)
    )
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if date_from is not None:
        q = q.filter(
            AuditLog.created_at
            >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to is not None:
        q = q.filter(
            AuditLog.created_at
            <= datetime.combine(date_to, datetime.max.time())
        )
    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )

    items = [
        AuditLogOut(
            id=row.AuditLog.id,
            user_id=row.AuditLog.user_id,
            user_email=row.email,
            action=row.AuditLog.action,
            entity_type=row.AuditLog.entity_type,
            entity_id=row.AuditLog.entity_id,
            old_value=row.AuditLog.old_value,
            new_value=row.AuditLog.new_value,
            changes=row.AuditLog.changes,
            # psycopg's INET adapter returns IPv4Address/IPv6Address objects;
            # pydantic won't coerce them to `str`, so stringify on the way out.
            ip_address=(
                str(row.AuditLog.ip_address)
                if row.AuditLog.ip_address is not None
                else None
            ),
            request_id=row.AuditLog.request_id,
            source=row.AuditLog.source,
            created_at=row.AuditLog.created_at,
        )
        for row in rows
    ]
    return AuditLogListResponse(
        items=items, meta={"next_cursor": None, "total": total}
    )
