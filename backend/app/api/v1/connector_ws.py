"""WS /api/v1/connector/ws — the cloud side of CONNECTOR_PROTOCOL.

Validates the connector token + X-Company-ID header at upgrade, then
runs the receive loop:

  `register`        → reply `register_ack` (registers in the
                      ConnectorRegistry)
  `heartbeat`       → reply `heartbeat_ack`; touch last_heartbeat_at
  `command_result`  → resolve the future stored by send_command()
  `tally_event`     → log informationally (Phase-0 no-op)
  `error`           → log

Close codes per CONNECTOR_PROTOCOL.md §"Close codes":
  4002 — token expired
  4003 — company mismatch (token.company_id ≠ X-Company-ID)
  4400 — protocol version unsupported
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.security import TokenExpired, TokenInvalid, decode_connector_token
from app.services.tally.connector_registry import (
    ConnectorConnection,
    get_registry,
)

logger = logging.getLogger("app.api.v1.connector_ws")

router = APIRouter(prefix="/connector", tags=["connector"])

SUPPORTED_PROTOCOL_VERSION = 1

# Local close codes from CONNECTOR_PROTOCOL.md
CLOSE_TOKEN_EXPIRED = 4002
CLOSE_COMPANY_MISMATCH = 4003
CLOSE_PROTOCOL_UNSUPPORTED = 4400


@router.websocket("/ws")
async def connector_ws(ws: WebSocket) -> None:
    """Long-lived connector socket. One per (company_id, connector_id)."""
    # ---- Headers ----
    auth = ws.headers.get("authorization") or ""
    bearer = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
    raw_company_id = ws.headers.get("x-company-id") or ""
    proto = ws.headers.get("x-protocol-version") or ""

    # ---- Validation BEFORE accept (close with code 4xxx) ----
    if proto and proto != str(SUPPORTED_PROTOCOL_VERSION):
        await ws.close(
            code=CLOSE_PROTOCOL_UNSUPPORTED,
            reason=f"protocol {proto} unsupported",
        )
        return

    try:
        payload = decode_connector_token(bearer)
    except TokenExpired:
        await ws.close(code=CLOSE_TOKEN_EXPIRED, reason="token expired")
        return
    except TokenInvalid:
        # CONNECTOR_PROTOCOL.md doesn't enumerate a code for "bad
        # signature / malformed". Use HTTP-401-equivalent 1008 (policy
        # violation). The connector will treat as terminal.
        await ws.close(code=1008, reason="invalid connector token")
        return

    try:
        token_company = UUID(payload.company_id)
    except ValueError:
        await ws.close(code=1008, reason="invalid company id in token")
        return

    if raw_company_id and raw_company_id != payload.company_id:
        await ws.close(
            code=CLOSE_COMPANY_MISMATCH,
            reason="X-Company-ID does not match token",
        )
        return

    try:
        connector_id = UUID(payload.sub)
    except ValueError:
        await ws.close(code=1008, reason="invalid connector id in token")
        return

    await ws.accept()

    conn = ConnectorConnection(
        company_id=token_company,
        connector_id=connector_id,
        ws=ws,
    )
    registry = get_registry()
    await registry.register(conn)

    try:
        await _run_message_loop(conn)
    except WebSocketDisconnect:
        pass
    finally:
        await registry.deregister(conn)


async def _run_message_loop(conn: ConnectorConnection) -> None:
    while True:
        raw = await conn.ws.receive_text()
        try:
            env = json.loads(raw)
        except json.JSONDecodeError:
            await _send_protocol_error(
                conn, "envelope not JSON", request_id=None
            )
            continue
        if not isinstance(env, dict):
            await _send_protocol_error(
                conn, "envelope not an object", request_id=None
            )
            continue
        missing = [
            k for k in ("type", "request_id", "ts", "payload") if k not in env
        ]
        if missing:
            await _send_protocol_error(
                conn,
                f"missing fields: {missing}",
                request_id=env.get("request_id"),
            )
            continue
        if not isinstance(env["payload"], dict):
            await _send_protocol_error(
                conn,
                "payload must be object",
                request_id=env["request_id"],
            )
            continue

        type_ = env["type"]
        payload = env["payload"]
        request_id = env["request_id"]

        if type_ == "register":
            await _handle_register(conn, request_id, payload)
        elif type_ == "heartbeat":
            await _handle_heartbeat(conn, request_id, payload)
        elif type_ == "command_result":
            conn.resolve_command_result(request_id, payload)
        elif type_ == "tally_event":
            logger.info(
                "tally_event from %s: %s", conn.company_id, payload
            )
        elif type_ == "error":
            logger.warning(
                "connector-side error %s: %s", conn.company_id, payload
            )
        else:
            await _send_protocol_error(
                conn, f"unknown type {type_!r}", request_id
            )


# ---------------------------------------------------------------------
# Per-type handlers
# ---------------------------------------------------------------------


async def _handle_register(
    conn: ConnectorConnection, request_id: str, payload: dict[str, Any]
) -> None:
    conn.tally_running = bool(payload.get("tally_running", True))
    conn.tally_version = payload.get("tally_version")
    conn.connector_version = payload.get("connector_version")
    conn.queued_outbound_count = int(payload.get("queued_outbound_count", 0))
    conn.touch_heartbeat()

    ack_payload = {
        "connector_id": str(conn.connector_id),
        "company_id": str(conn.company_id),
        "server_version": "0.1.0",
        "protocol_version": SUPPORTED_PROTOCOL_VERSION,
    }
    await _send_with_request_id(
        conn, type_="register_ack", request_id=request_id, payload=ack_payload
    )


async def _handle_heartbeat(
    conn: ConnectorConnection, request_id: str, payload: dict[str, Any]
) -> None:
    conn.tally_running = bool(payload.get("tally_running", conn.tally_running))
    conn.queued_outbound_count = int(
        payload.get("queued_outbound_count", conn.queued_outbound_count)
    )
    conn.touch_heartbeat()
    await _send_with_request_id(
        conn, type_="heartbeat_ack", request_id=request_id, payload={}
    )


# ---------------------------------------------------------------------
# Outbound helpers
# ---------------------------------------------------------------------


async def _send_with_request_id(
    conn: ConnectorConnection,
    *,
    type_: str,
    request_id: str,
    payload: dict[str, Any],
) -> None:
    from datetime import UTC, datetime

    env = {
        "type": type_,
        "request_id": request_id,
        "ts": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    await conn.ws.send_text(json.dumps(env, separators=(",", ":")))


async def _send_protocol_error(
    conn: ConnectorConnection,
    message: str,
    request_id: str | None,
) -> None:
    from datetime import UTC, datetime

    env = {
        "type": "error",
        "request_id": request_id or "",
        "ts": datetime.now(UTC).isoformat(),
        "payload": {"code": "protocol_error", "message": message},
    }
    try:
        await conn.ws.send_text(json.dumps(env, separators=(",", ":")))
    except Exception:  # noqa: BLE001 — error path; nothing else to do
        pass
