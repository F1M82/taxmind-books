"""End-to-end WS-client tests against a fake backend.

A `websockets` server stands in for the cloud backend. It receives
the `register`, replies `register_ack`, then sends one `command`
and expects a `command_result`.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
import websockets

from connector.envelope import build_envelope, parse_envelope
from connector.tally_client import TallyClient
from connector.ws_client import ConnectorWSClient


def _fake_tally() -> TallyClient:
    """A TallyClient with a stub `ping` that returns True without
    a network call. The other methods aren't reached in these tests."""
    from unittest.mock import AsyncMock

    c = TallyClient(host="x", port=9000)
    c.ping = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return c


@asynccontextmanager
async def _serve(handler):  # type: ignore[no-untyped-def]
    """Run a fake backend WS server on an ephemeral port."""
    server = await websockets.serve(handler, host="127.0.0.1", port=0)
    sock = next(iter(server.sockets))
    port = sock.getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


# ---------------- happy-path register + command + reply ----------------


@pytest.mark.asyncio
async def test_register_then_command_then_command_result() -> None:
    company_id = str(uuid4())
    received: dict[str, Any] = {}
    command_done = asyncio.Event()

    async def handler(ws):  # type: ignore[no-untyped-def]
        # 1) Receive register, send register_ack with company_id.
        env = parse_envelope(await ws.recv())
        assert env["type"] == "register"
        await ws.send(
            build_envelope(
                type_="register_ack",
                payload={
                    "connector_id": str(uuid4()),
                    "company_id": company_id,
                    "company_name": "Acme",
                    "server_version": "0.1.0",
                    "protocol_version": 1,
                },
            )
        )

        # 2) Issue a `command: ping` and wait for the result.
        cmd_request_id = str(uuid4())
        await ws.send(
            json.dumps(
                {
                    "type": "command",
                    "request_id": cmd_request_id,
                    "ts": "2026-05-08T10:00:00Z",
                    "payload": {
                        "company_id": company_id,
                        "command": "ping",
                        "args": {},
                    },
                }
            )
        )

        # 3) Receive command_result; assert request_id is echoed.
        result_env = parse_envelope(await ws.recv())
        assert result_env["type"] == "command_result"
        assert result_env["request_id"] == cmd_request_id
        received["result"] = result_env["payload"]
        command_done.set()
        # Hold the connection long enough for the heartbeat loop NOT
        # to fire (we don't need it for this test).
        await asyncio.sleep(0.1)

    async with _serve(handler) as url:
        client = ConnectorWSClient(
            ws_url=url,
            connector_token="tok",
            company_id=company_id,
            tally=_fake_tally(),
            heartbeat_seconds=999,  # avoid heartbeat in this run
            initial_backoff=0.05,
        )
        run_task = asyncio.create_task(
            client.run_forever(max_iterations=1)
        )
        try:
            await asyncio.wait_for(command_done.wait(), timeout=5.0)
        finally:
            client.stop()
            await asyncio.wait_for(run_task, timeout=5.0)

    assert received["result"]["status"] == "success"
    assert received["result"]["result"]["tally_responsive"] is True


# ---------------- company mismatch ----------------


@pytest.mark.asyncio
async def test_command_with_wrong_company_id_returns_company_mismatch() -> None:
    registered_company = str(uuid4())
    other_company = str(uuid4())
    received: dict[str, Any] = {}
    command_done = asyncio.Event()

    async def handler(ws):  # type: ignore[no-untyped-def]
        env = parse_envelope(await ws.recv())
        assert env["type"] == "register"
        await ws.send(
            build_envelope(
                type_="register_ack",
                payload={
                    "connector_id": str(uuid4()),
                    "company_id": registered_company,
                    "protocol_version": 1,
                },
            )
        )

        # Send a command for a DIFFERENT company.
        await ws.send(
            json.dumps(
                {
                    "type": "command",
                    "request_id": str(uuid4()),
                    "ts": "2026-05-08T10:00:00Z",
                    "payload": {
                        "company_id": other_company,
                        "command": "ping",
                        "args": {},
                    },
                }
            )
        )

        result_env = parse_envelope(await ws.recv())
        received["result"] = result_env["payload"]
        command_done.set()
        await asyncio.sleep(0.1)

    async with _serve(handler) as url:
        client = ConnectorWSClient(
            ws_url=url,
            connector_token="tok",
            company_id=registered_company,
            tally=_fake_tally(),
            heartbeat_seconds=999,
        )
        run_task = asyncio.create_task(
            client.run_forever(max_iterations=1)
        )
        try:
            await asyncio.wait_for(command_done.wait(), timeout=5.0)
        finally:
            client.stop()
            await asyncio.wait_for(run_task, timeout=5.0)

    assert received["result"]["status"] == "error"
    assert received["result"]["error"]["code"] == "company_mismatch"


# ---------------- terminal close code ----------------


@pytest.mark.asyncio
async def test_terminal_close_code_4001_stops_reconnect() -> None:
    """Server closes with code 4001 (revoked) immediately on connect.
    The client must NOT loop back; run_forever returns."""

    async def handler(ws):  # type: ignore[no-untyped-def]
        await ws.close(code=4001, reason="revoked")

    async with _serve(handler) as url:
        client = ConnectorWSClient(
            ws_url=url,
            connector_token="tok",
            company_id=str(uuid4()),
            tally=_fake_tally(),
            heartbeat_seconds=999,
            initial_backoff=0.01,
        )
        # `run_forever` returns when terminal — should complete fast.
        await asyncio.wait_for(client.run_forever(), timeout=5.0)
