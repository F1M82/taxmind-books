"""Unit tests for ConnectorRegistry + ConnectorConnection.

Uses a fake WebSocket so we can drive everything inside one event loop.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorConnection,
    ConnectorOffline,
    ConnectorRegistry,
)


class _FakeWS:
    """Minimal duck-type for `ws.send_text` + `ws.close`."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


# ---------------- send_command + resolve ----------------


@pytest.mark.asyncio
async def test_send_command_resolves_when_result_arrives() -> None:
    ws = _FakeWS()
    company_id = uuid4()
    conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=ws  # type: ignore[arg-type]
    )
    registry = ConnectorRegistry()
    await registry.register(conn)

    async def reply_after_send() -> None:
        # Wait one loop tick for send_command to fire.
        while not ws.sent:
            await asyncio.sleep(0)
        env = json.loads(ws.sent[0])
        assert env["type"] == "command"
        assert env["payload"]["command"] == "ping"
        conn.resolve_command_result(
            env["request_id"],
            {"command": "ping", "status": "success", "result": {"ok": True}},
        )

    asyncio.create_task(reply_after_send())
    result = await registry.send_command(
        company_id=company_id,
        command="ping",
        args={},
        timeout_seconds=2,
    )
    assert result["status"] == "success"
    assert result["result"]["ok"] is True


@pytest.mark.asyncio
async def test_send_command_times_out_when_no_reply() -> None:
    ws = _FakeWS()
    company_id = uuid4()
    conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=ws  # type: ignore[arg-type]
    )
    registry = ConnectorRegistry()
    await registry.register(conn)

    with pytest.raises(CommandTimeout):
        await registry.send_command(
            company_id=company_id,
            command="ping",
            args={},
            timeout_seconds=0.1,
        )


@pytest.mark.asyncio
async def test_send_command_raises_when_no_connector() -> None:
    registry = ConnectorRegistry()
    with pytest.raises(ConnectorOffline):
        await registry.send_command(
            company_id=uuid4(), command="ping", args={}
        )


# ---------------- replace-stale-on-register ----------------


@pytest.mark.asyncio
async def test_register_replaces_stale_connection() -> None:
    company_id = uuid4()
    old_ws = _FakeWS()
    new_ws = _FakeWS()
    old_conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=old_ws  # type: ignore[arg-type]
    )
    new_conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=new_ws  # type: ignore[arg-type]
    )

    registry = ConnectorRegistry()
    await registry.register(old_conn)
    await registry.register(new_conn)

    assert registry.get(company_id) is new_conn
    assert old_ws.closed == (4429, "superseded")


@pytest.mark.asyncio
async def test_register_cancels_pending_on_replace() -> None:
    company_id = uuid4()
    old_ws = _FakeWS()
    new_ws = _FakeWS()
    old_conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=old_ws  # type: ignore[arg-type]
    )

    registry = ConnectorRegistry()
    await registry.register(old_conn)

    # Start a send_command that will block waiting for a reply.
    task = asyncio.create_task(
        registry.send_command(
            company_id=company_id,
            command="ping",
            args={},
            timeout_seconds=10,
        )
    )
    # Yield so the future gets registered.
    await asyncio.sleep(0)

    # Replace the connection — old pending should be cancelled.
    new_conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=new_ws  # type: ignore[arg-type]
    )
    await registry.register(new_conn)

    with pytest.raises(ConnectorOffline):
        await task


# ---------------- deregister ----------------


@pytest.mark.asyncio
async def test_deregister_removes_entry_and_cancels_pending() -> None:
    company_id = uuid4()
    ws = _FakeWS()
    conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=ws  # type: ignore[arg-type]
    )
    registry = ConnectorRegistry()
    await registry.register(conn)

    task = asyncio.create_task(
        registry.send_command(
            company_id=company_id, command="ping", args={}, timeout_seconds=10
        )
    )
    await asyncio.sleep(0)

    await registry.deregister(conn)
    assert registry.get(company_id) is None
    with pytest.raises(ConnectorOffline):
        await task


# ---------------- status / online ----------------


@pytest.mark.asyncio
async def test_status_for_returns_snapshot() -> None:
    company_id = uuid4()
    ws = _FakeWS()
    conn = ConnectorConnection(
        company_id=company_id, connector_id=uuid4(), ws=ws  # type: ignore[arg-type]
    )
    conn.tally_running = True
    conn.tally_version = "3.0"
    conn.connector_version = "1.0.0"
    conn.queued_outbound_count = 2

    registry = ConnectorRegistry()
    await registry.register(conn)

    snap = registry.status_for(company_id)
    assert snap is not None
    assert snap["connected"] is True
    assert snap["tally_running"] is True
    assert snap["tally_version"] == "3.0"
    assert snap["queued_outbound_count"] == 2


def test_status_for_returns_none_for_unknown() -> None:
    registry = ConnectorRegistry()
    assert registry.status_for(uuid4()) is None


# ---------------- unsolicited / late command_result ignored ----------------


@pytest.mark.asyncio
async def test_resolve_command_result_for_unknown_request_id_returns_false() -> None:
    ws = _FakeWS()
    conn = ConnectorConnection(
        company_id=uuid4(), connector_id=uuid4(), ws=ws  # type: ignore[arg-type]
    )
    assert conn.resolve_command_result("does-not-exist", {"x": 1}) is False
