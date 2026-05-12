"""Firebase Cloud Messaging client (P0.44 stub).

Phase 0 ships the registration plumbing only; this module is a no-op
shim that satisfies `notification_service.send_to_user` without
talking to FCM. Tests monkey-patch `send_push` to assert dispatch
behaviour.

When Phase 1 lights up real notifications, replace `send_push` with
an httpx.AsyncClient POST to https://fcm.googleapis.com/v1/projects/
{project}/messages:send, JWT-signed with the service-account
credential. Don't add the real call here piecemeal — that's
operational state (creds, project ID, retries) that needs a config
surface and proper observability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("app.integrations.fcm")


@dataclass(frozen=True)
class FcmResult:
    delivered: bool
    error: str | None = None


def send_push(
    *,
    token: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
) -> FcmResult:
    """Send one push to one FCM token.

    Phase-0 stub: logs the intent and reports success without
    network I/O. Tests monkey-patch the module's `send_push` to
    assert dispatch decisions.
    """
    logger.info(
        "fcm.send_push (stub)",
        extra={
            "token_suffix": token[-6:] if len(token) >= 6 else token,
            "title": title,
        },
    )
    _ = (body, data)  # consumed by the real client in Phase 1
    return FcmResult(delivered=True)


__all__ = ["FcmResult", "send_push"]
