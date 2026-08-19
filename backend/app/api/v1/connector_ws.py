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

import contextlib
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.database import SessionLocal
from app.core.security import TokenExpired, TokenInvalid, decode_connector_token
from app.models.connector import Connector, ConnectorCompanyBinding
from app.services.tally import connector_registry as _connector_registry_mod
from app.services.tally.connector_registry import ConnectorConnection

logger = logging.getLogger("app.api.v1.connector_ws")

router = APIRouter(prefix="/connector", tags=["connector"])

SUPPORTED_PROTOCOL_VERSION = 1

# Local close codes from CONNECTOR_PROTOCOL.md
CLOSE_TOKEN_EXPIRED = 4002
CLOSE_COMPANY_MISMATCH = 4003
CLOSE_PROTOCOL_UNSUPPORTED = 4400


@router.websocket("/ws")
async def connector_ws(ws: WebSocket) -> None:  # noqa: PLR0912, PLR0915
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
        connector_id = UUID(payload.sub)
    except ValueError:
        await ws.close(code=1008, reason="invalid connector id in token")
        return

    # Legacy tokens are company-bound and retain their original validation.
    # New installation-scoped tokens must resolve their provisioning scope
    # from the persisted connector; the header is never tenant authority.
    token_company: UUID | None = None
    if payload.company_id is not None:
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

    db = SessionLocal()
    try:
        connector = db.query(Connector).filter(Connector.id == connector_id).first()
        if payload.company_id is None:
            if connector is None or connector.enrolled_company_id is None:
                await ws.close(code=1008, reason="connector not found")
                return
            if not raw_company_id or raw_company_id != str(connector.enrolled_company_id):
                await ws.close(
                    code=CLOSE_COMPANY_MISMATCH,
                    reason="X-Company-ID does not match connector provisioning scope",
                )
                return
            token_company = connector.enrolled_company_id

        binding_company_ids = {
            row[0]
            for row in db.query(ConnectorCompanyBinding.company_id).filter(
                ConnectorCompanyBinding.connector_id == connector_id
            ).all()
        }
    finally:
        db.close()

    assert token_company is not None

    await ws.accept()

    conn = ConnectorConnection(
        company_id=token_company,
        connector_id=connector_id,
        ws=ws,
        authorized_company_ids=binding_company_ids | {token_company},
    )
    registry = _connector_registry_mod.get_registry()
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
        elif type_ == "tally_company_changed":
            logger.info(
                "tally company changed on connector %s: %s",
                conn.connector_id,
                payload,
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
    conn.connector_build_sha = payload.get("connector_build_sha")
    conn.connector_built_at = payload.get("connector_built_at")
    conn.queued_outbound_count = int(payload.get("queued_outbound_count", 0))
    conn.touch_heartbeat()

    ack_payload = {
        "connector_id": str(conn.connector_id),
        "company_id": str(conn.company_id),
        "authorized_target_company_ids": [
            str(company_id)
            for company_id in sorted(conn.authorized_company_ids, key=str)
        ],
        "server_version": "0.1.0",
        "protocol_version": SUPPORTED_PROTOCOL_VERSION,
    }
    await _send_with_request_id(
        conn, type_="register_ack", request_id=request_id, payload=ack_payload
    )

    # BUG-Books-002: the connector is back and Tally is running — this is
    # the exact moment an outage ends (laptop reopened, Tally restarted).
    # Re-dispatch this company's retryable-class stranded vouchers. Runs
    # in-process (this uvicorn owns the WS + the connector registry), so
    # the dispatch can actually reach the connector.
    if conn.tally_running:
        _schedule_reenqueue_on_connector_up(conn.company_id)


def _schedule_reenqueue_on_connector_up(company_id: UUID) -> None:
    """Fire-and-forget re-enqueue of one company's retryable strands.

    Opens its own ``SessionLocal`` (the WS handler holds no request
    session) and runs on the current event loop. Gated by
    ``TAXMIND_SKIP_TALLY_DISPATCH`` so the test suite never dispatches.
    """
    from app.config import get_settings

    if get_settings().TAXMIND_SKIP_TALLY_DISPATCH:
        return

    import asyncio

    from app.services.tally.voucher_reenqueue import (
        reenqueue_retryable_vouchers,
    )

    async def _drive() -> None:
        db = SessionLocal()
        try:
            await reenqueue_retryable_vouchers(db, company_id=company_id)
        except Exception:
            logger.exception(
                "connector-up re-enqueue failed for company %s", company_id
            )
        finally:
            db.close()

    asyncio.get_running_loop().create_task(_drive())


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
    with contextlib.suppress(Exception):
        await conn.ws.send_text(json.dumps(env, separators=(",", ":")))
