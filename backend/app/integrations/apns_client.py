"""Apple Push Notification service client (P0.44 stub).

Phase 0 ships the registration plumbing only. See
`integrations/fcm_client.py` for the rationale; the same notes
apply.

Phase 1 replaces `send_push` with an httpx.AsyncClient call to
APNs's HTTP/2 endpoint using the team's APNs JWT (ES256 over the
team-id / key-id / topic claims). Don't add real I/O here without
the surrounding config + observability surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("app.integrations.apns")


@dataclass(frozen=True)
class ApnsResult:
    delivered: bool
    error: str | None = None


def send_push(
    *,
    token: str,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
) -> ApnsResult:
    """Send one push to one APNs device token (stub)."""
    logger.info(
        "apns.send_push (stub)",
        extra={
            "token_suffix": token[-6:] if len(token) >= 6 else token,
            "title": title,
        },
    )
    _ = (body, data)
    return ApnsResult(delivered=True)


__all__ = ["ApnsResult", "send_push"]
