"""Per-user push notification dispatch (P0.44).

`send_to_user(db, user_id, notification)` fans out a single
`Notification` to every active `DeviceToken` for the user, routed
to FCM (android/web) or APNs (ios) based on platform. Phase 0 has
no triggers wired in production code — this service exists so
Phase 1 can plug `notification_service.send_to_user(...)` into a
voucher-approved or invoice-extracted hook without rewiring.

Module-level imports of `fcm_client` and `apns_client` follow the
patchable-singletons convention from CONNECTOR_PROTOCOL.md: tests
monkey-patch the integrations modules and the dispatch picks up
the patched `send_push`.

Errors from individual sends are captured and returned; the caller
(usually a worker) decides whether to retry, deactivate the token,
or surface to the user. Phase 0 never reaches the error branch
because the stub clients always succeed; Phase 1's real clients
will populate `error` from the provider response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations import apns_client as _apns_mod
from app.integrations import fcm_client as _fcm_mod
from app.models.device_token import DevicePlatform, DeviceToken

logger = logging.getLogger("app.services.notification")


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    data: dict[str, str] | None = None


@dataclass(frozen=True)
class DispatchResult:
    device_id: UUID
    platform: str
    delivered: bool
    error: str | None = None


def send_to_user(  # audit-exempt: read-only dispatch; registration emits
    db: Session, *, user_id: UUID, notification: Notification
) -> list[DispatchResult]:
    """Dispatch `notification` to every active token of `user_id`.

    Returns one `DispatchResult` per target token. No-op (empty
    list) when the user has no active devices. Inactive tokens
    (`is_active=false`) are intentionally skipped — the
    unregister flow flips that bit and we honor it here.
    """
    rows = (
        db.execute(
            select(DeviceToken).where(
                DeviceToken.user_id == user_id,
                DeviceToken.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )

    results: list[DispatchResult] = []
    for row in rows:
        if row.platform == DevicePlatform.ios:
            r = _apns_mod.send_push(
                token=row.token,
                title=notification.title,
                body=notification.body,
                data=notification.data,
            )
        else:
            # android + web both route through FCM by current convention.
            r = _fcm_mod.send_push(
                token=row.token,
                title=notification.title,
                body=notification.body,
                data=notification.data,
            )
        results.append(
            DispatchResult(
                device_id=row.id,
                platform=row.platform.value,
                delivered=r.delivered,
                error=r.error,
            )
        )

    return results


__all__ = ["DispatchResult", "Notification", "send_to_user"]
