"""Integration tests for WS /api/v1/connector/ws (P0.24).

Uses Starlette's WebSocketTestSession (synchronous wrapper). The
backend WS endpoint validates the connector token, runs handshake,
register/heartbeat, and command dispatch.

Command-dispatch end-to-end coverage (registry.send_command across
the WS) is hard to drive from the sync TestClient — see the
unit-test suite tests/unit/services/test_connector_registry.py
which exercises send_command + futures against a fake WebSocket.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest
from app.config import get_settings
from app.core.security import (
    CONNECTOR_TOKEN_KIND,
    create_connector_token,
)
from app.services.tally.connector_registry import get_registry
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Make sure each test starts with no live connector entries."""
    reg = get_registry()
    reg._by_company.clear()
    yield
    reg._by_company.clear()


def _wait_for_registry(company_id, timeout: float = 1.0):  # type: ignore[no-untyped-def]
    """Spin briefly until the WS handler has stashed the connection in
    the registry. The accept-then-register sequence is async, so the
    sync TestClient may return from `websocket_connect` before the
    server-side has finished registering."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = get_registry().get(company_id)
        if conn is not None:
            return conn
        time.sleep(0.01)
    raise AssertionError("connector did not register within deadline")


def _build_envelope(
    *, type_: str, payload, request_id: str | None = None  # type: ignore[no-untyped-def]
) -> str:
    return json.dumps(
        {
            "type": type_,
            "request_id": request_id or str(uuid4()),
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
    )


def _open(
    client: TestClient,
    *,
    token: str,
    company_id,  # type: ignore[no-untyped-def]
    protocol_version: str = "1",
) -> WebSocketTestSession:
    return client.websocket_connect(
        "/api/v1/connector/ws",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Company-ID": str(company_id),
            "X-Protocol-Version": protocol_version,
            "X-Connector-Version": "1.0.0",
        },
    )


# ---------------- successful register ----------------


def test_register_and_register_ack(client: TestClient) -> None:
    connector_id = uuid4()
    company_id = uuid4()
    token = create_connector_token(
        connector_id=connector_id, company_id=company_id
    )
    with _open(client, token=token, company_id=company_id) as ws:
        ws.send_text(
            _build_envelope(
                type_="register",
                payload={
                    "connector_version": "1.0.0",
                    "protocol_version": 1,
                    "tally_running": True,
                    "tally_version": "3.0",
                    "queued_outbound_count": 0,
                },
            )
        )
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "register_ack"
        assert ack["payload"]["company_id"] == str(company_id)
        assert ack["payload"]["connector_id"] == str(connector_id)
        assert ack["payload"]["protocol_version"] == 1

        conn = _wait_for_registry(company_id)
        assert conn.connector_id == connector_id
        assert conn.tally_version == "3.0"


# ---------------- heartbeat / heartbeat_ack ----------------


def test_heartbeat_returns_heartbeat_ack(client: TestClient) -> None:
    company_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with _open(client, token=token, company_id=company_id) as ws:
        # Skip past register; the protocol allows heartbeat after
        # register_ack but the WS handler doesn't require it.
        ws.send_text(
            _build_envelope(
                type_="register",
                payload={"connector_version": "1.0.0", "protocol_version": 1},
            )
        )
        json.loads(ws.receive_text())  # register_ack

        ws.send_text(
            _build_envelope(
                type_="heartbeat",
                payload={"tally_running": True, "queued_outbound_count": 5},
            )
        )
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "heartbeat_ack"

        conn = _wait_for_registry(company_id)
        assert conn.queued_outbound_count == 5


# ---------------- close codes ----------------


def test_company_mismatch_closes_with_4003(client: TestClient) -> None:
    """X-Company-ID header differs from token's company_id → 4003."""
    company_id = uuid4()
    other_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with pytest.raises(Exception) as exc_info, _open(client, token=token, company_id=other_id):
        pass
    # WebSocketDisconnect carries the close code.
    assert getattr(exc_info.value, "code", None) == 4003


def test_invalid_token_closes(client: TestClient) -> None:
    with (
        pytest.raises(Exception) as exc_info,
        _open(client, token="not.a.real.token", company_id=uuid4()),
    ):
        pass
    assert getattr(exc_info.value, "code", None) == 1008


def test_expired_token_closes_with_4002(client: TestClient) -> None:
    cfg = get_settings()
    now = int(time.time()) - 10
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "company_id": str(uuid4()),
            "kind": CONNECTOR_TOKEN_KIND,
            "iat": now - 60,
            "exp": now,
        },
        cfg.CONNECTOR_JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(Exception) as exc_info, _open(client, token=expired, company_id=uuid4()):
        pass
    assert getattr(exc_info.value, "code", None) == 4002


def test_user_token_kind_rejected(client: TestClient) -> None:
    """A user JWT presented to the connector WS is not a connector
    token (kind != 'connector') → closed."""
    cfg = get_settings()
    bogus = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",  # user-token style
            "iat": int(time.time()),
            "exp": int(time.time()) + 1800,
        },
        cfg.CONNECTOR_JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(Exception) as exc_info, _open(client, token=bogus, company_id=uuid4()):
        pass
    assert getattr(exc_info.value, "code", None) == 1008


def test_unsupported_protocol_version_closes_with_4400(
    client: TestClient,
) -> None:
    company_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with pytest.raises(Exception) as exc_info, _open(
        client, token=token, company_id=company_id, protocol_version="99"
    ):
        pass
    assert getattr(exc_info.value, "code", None) == 4400


# ---------------- protocol_error ----------------


def test_malformed_envelope_triggers_protocol_error_message(
    client: TestClient,
) -> None:
    company_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with _open(client, token=token, company_id=company_id) as ws:
        ws.send_text("not json at all")
        err = json.loads(ws.receive_text())
        assert err["type"] == "error"
        assert err["payload"]["code"] == "protocol_error"


def test_missing_required_field_triggers_protocol_error(
    client: TestClient,
) -> None:
    company_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with _open(client, token=token, company_id=company_id) as ws:
        ws.send_text(json.dumps({"type": "register", "request_id": "x"}))
        err = json.loads(ws.receive_text())
        assert err["type"] == "error"
        assert err["payload"]["code"] == "protocol_error"


# ---------------- command_result resolves a pending future ----------------


def test_command_result_resolves_pending_future(client: TestClient) -> None:
    """When the WS receive loop sees a command_result, it pops the
    corresponding future from `conn.pending`. We seed a pending
    future, send the matching result, and verify completion."""
    import asyncio

    company_id = uuid4()
    token = create_connector_token(
        connector_id=uuid4(), company_id=company_id
    )
    with _open(client, token=token, company_id=company_id) as ws:
        ws.send_text(
            _build_envelope(
                type_="register",
                payload={"connector_version": "1.0.0", "protocol_version": 1},
            )
        )
        json.loads(ws.receive_text())

        conn = _wait_for_registry(company_id)
        # Seed a pending future on the same event loop as the WS.
        loop = conn.ws.app.state if False else None  # noqa: F841 — see comment
        # The connection's event loop is the FastAPI app loop. To
        # create a future on it from the test thread, use
        # asyncio.run_coroutine_threadsafe via the portal. Starlette's
        # TestClient stashes the portal on the test-session; simpler:
        # use the conn directly with a synchronous future-creation
        # helper that posts onto its loop.
        from concurrent.futures import Future as CFuture

        cfut: CFuture = CFuture()

        async def setup_pending():  # type: ignore[no-untyped-def]
            fut = asyncio.get_event_loop().create_future()
            conn.pending["rid-seeded"] = fut
            cfut.set_result(fut)

        # Run the coroutine on the WS's loop via the running app's
        # portal. We reach the portal via ws.portal which Starlette's
        # test session exposes.
        ws.portal.start_task_soon(setup_pending)
        seeded = cfut.result(timeout=2.0)

        # Send a command_result with that request_id.
        ws.send_text(
            json.dumps(
                {
                    "type": "command_result",
                    "request_id": "rid-seeded",
                    "ts": datetime.now(UTC).isoformat(),
                    "payload": {
                        "command": "ping",
                        "status": "success",
                        "result": {"tally_responsive": True},
                    },
                }
            )
        )

        # Wait for the future to resolve.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not seeded.done():
            time.sleep(0.01)
        assert seeded.done()
        result = seeded.result()
        assert result["status"] == "success"
        assert result["result"]["tally_responsive"] is True
