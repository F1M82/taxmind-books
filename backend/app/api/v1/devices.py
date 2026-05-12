"""Devices endpoints (P0.44).

POST /api/v1/devices/register     — register or refresh a token.
DELETE /api/v1/devices/{device_id} — deactivate a token.

Both operations are user-scoped (no X-Company-ID). The user must be
authenticated; the device row's `user_id` is taken from the JWT,
not from the request body, so a client can't register a token
against another user.

Re-registering the same token is idempotent: the row's
`is_active` is flipped back to true and `last_active_at` is
bumped. Re-registering a token currently owned by a DIFFERENT
user re-points it; this matches the typical "user A logs out,
user B logs in on the same device" mobile flow. The audit log
captures both transitions so the reassignment is auditable.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.audit import AuditContext, AuditEmitter
from app.core.database import get_db
from app.core.exceptions import NotFound
from app.models.device_token import DevicePlatform, DeviceToken
from app.models.user import User
from app.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
)

router = APIRouter(prefix="/devices", tags=["devices"])


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


def _user_audit_emitter(
    request: Request, db: Session, user: User
) -> AuditEmitter:
    return AuditEmitter(
        db,
        AuditContext(
            # Devices are user-scoped, not company-scoped — system event.
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
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceRegisterResponse,
)
def register_device(
    data: DeviceRegisterRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeviceRegisterResponse:
    audit = _user_audit_emitter(request, db, user)

    existing = db.scalar(
        select(DeviceToken).where(DeviceToken.token == data.token)
    )
    if existing is not None:
        # Idempotent re-registration: flip back to active, bump
        # last_active_at, and update the metadata we know about.
        # If a different user previously held this token (device
        # changed hands), reassign the row and audit both the
        # transition and the new owner.
        previous_user_id = existing.user_id
        existing.user_id = user.id
        existing.platform = DevicePlatform(data.platform)
        existing.app_version = data.app_version
        existing.is_active = True
        existing.last_active_at = datetime.now(UTC)
        db.flush()
        audit.emit(
            action="device.registered",
            entity_type="device_token",
            entity_id=existing.id,
            old_value={
                "previous_user_id": str(previous_user_id),
                "is_active": False,
            },
            new_value={
                "user_id": str(user.id),
                "platform": data.platform,
                "app_version": data.app_version,
            },
            actor_user_id=user.id,
        )
        db.commit()
        return DeviceRegisterResponse(id=existing.id, token_registered=True)

    row = DeviceToken(
        user_id=user.id,
        token=data.token,
        platform=DevicePlatform(data.platform),
        app_version=data.app_version,
        is_active=True,
        last_active_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    audit.emit(
        action="device.registered",
        entity_type="device_token",
        entity_id=row.id,
        old_value=None,
        new_value={
            "user_id": str(user.id),
            "platform": data.platform,
            "app_version": data.app_version,
        },
        actor_user_id=user.id,
    )
    db.commit()
    return DeviceRegisterResponse(id=row.id, token_registered=True)


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unregister_device(
    device_id: UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.scalar(
        select(DeviceToken).where(
            DeviceToken.id == device_id,
            DeviceToken.user_id == user.id,
        )
    )
    if row is None:
        # Per TENANCY.md: hide existence from non-owners by collapsing
        # both "doesn't exist" and "belongs to another user" into 404.
        raise NotFound("Device not found.")

    if not row.is_active:
        # Already deactivated; treat as success (204) without writing a
        # redundant audit row.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    audit = _user_audit_emitter(request, db, user)
    row.is_active = False
    db.flush()
    audit.emit(
        action="device.unregistered",
        entity_type="device_token",
        entity_id=row.id,
        old_value={"is_active": True},
        new_value={"is_active": False},
        actor_user_id=user.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
