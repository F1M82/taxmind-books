"""End-to-end idempotency tests through the real WS client path.

Unlike session 2's unit tests (which call `dispatch_command` directly),
these drive a real `ConnectorWSClient` against a fake backend WS server:
register → command → command_result, with a process-singleton
IdempotencyCache (temp file) wired in exactly as `main.py` wires it. The
Tally client is mocked — no real Tally, no real Redis.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from connector.envelope import build_envelope, parse_envelope
from connector.idempotency_cache import IdempotencyCache
from connector.tally_client import TallyClient, TallyUnreachable
from connector.ws_client import ConnectorWSClient


def _mock_tally(
    *,
    post_result: dict[str, Any] | None = None,
    post_side_effect: Any | None = None,
) -> TallyClient:
    """A TallyClient with `ping` + `post_voucher` mocked (no network)."""
    c = TallyClient(host="x", port=9000)
    c.ping = AsyncMock(return_value=True)  # type: ignore[method-assign]
    c.post_voucher = AsyncMock(  # type: ignore[method-assign]
        return_value=post_result, side_effect=post_side_effect
    )
    return c


def _pv_payload(company_id: str, idem_key: str) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "command": "post_voucher",
        "args": {
            "voucher_type": "Receipt",
            "date": "2026-05-08",
            "voucher_number": "R-1",
            "party_name": "Sharma",
            "narration": "Payment received",
            "entries": [
                {
                    "ledger_name": "Bank",
                    "amount": "1000.00",
                    "entry_type": "Dr",
                },
                {
                    "ledger_name": "Sharma",
                    "amount": "1000.00",
                    "entry_type": "Cr",
                },
            ],
        },
        "idempotency_key": idem_key,
    }


def _command_env(payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": "command",
            "request_id": str(uuid4()),
            "ts": "2026-05-08T10:00:00Z",
            "payload": payload,
        }
    )


@asynccontextmanager
async def _serve(handler):  # type: ignore[no-untyped-def]
    import websockets

    server = await websockets.serve(handler, host="127.0.0.1", port=0)
    sock = next(iter(server.sockets))
    port = sock.getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


async def _drive(client: ConnectorWSClient, done: asyncio.Event) -> None:
    run_task = asyncio.create_task(client.run_forever(max_iterations=1))
    try:
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        client.stop()
        await asyncio.wait_for(run_task, timeout=5.0)


# ---------------------------------------------------------------------
# Replay: same idempotency_key → cached result, no second Tally call
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_voucher_replay_returns_cached_without_second_call(
    tmp_path: Path,
) -> None:
    company_id = str(uuid4())
    idem_key = "voucher-e2e-replay"
    payload = _pv_payload(company_id, idem_key)
    results: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def handler(ws):  # type: ignore[no-untyped-def]
        env = parse_envelope(await ws.recv())
        assert env["type"] == "register"
        await ws.send(
            build_envelope(
                type_="register_ack",
                payload={"company_id": company_id, "protocol_version": 1},
            )
        )
        # First post — should hit Tally.
        await ws.send(_command_env(payload))
        results.append(parse_envelope(await ws.recv())["payload"])
        # Replay with the same idempotency_key — should be served cached.
        await ws.send(_command_env(payload))
        results.append(parse_envelope(await ws.recv())["payload"])
        done.set()
        await asyncio.sleep(0.05)

    tally = _mock_tally(
        post_result={"status": "success", "tally_voucher_guid": "g1"}
    )
    cache = IdempotencyCache(tmp_path / "connector.db")
    try:
        async with _serve(handler) as url:
            client = ConnectorWSClient(
                ws_url=url,
                connector_token="tok",
                company_id=company_id,
                tally=tally,
                heartbeat_seconds=999,
                initial_backoff=0.05,
                cache=cache,
            )
            await _drive(client, done)
    finally:
        cache.close()

    assert len(results) == 2
    assert results[0]["status"] == "success"
    # Replay returns an envelope identical to the original.
    assert results[0] == results[1]
    # The hard idempotency assertion: Tally was hit exactly once.
    assert tally.post_voucher.call_count == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------
# Retry: retryable failure deletes the row → next attempt re-executes
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_voucher_retryable_failure_reexecutes_next_attempt(
    tmp_path: Path,
) -> None:
    company_id = str(uuid4())
    idem_key = "voucher-e2e-retry"
    payload = _pv_payload(company_id, idem_key)
    results: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def handler(ws):  # type: ignore[no-untyped-def]
        env = parse_envelope(await ws.recv())
        assert env["type"] == "register"
        await ws.send(
            build_envelope(
                type_="register_ack",
                payload={"company_id": company_id, "protocol_version": 1},
            )
        )
        # First attempt: transient failure (retryable) → row deleted.
        await ws.send(_command_env(payload))
        results.append(parse_envelope(await ws.recv())["payload"])
        # Second attempt: treated as first-seen, re-executes, succeeds.
        await ws.send(_command_env(payload))
        results.append(parse_envelope(await ws.recv())["payload"])
        done.set()
        await asyncio.sleep(0.05)

    # First post_voucher raises retryable; second returns success.
    tally = _mock_tally(
        post_side_effect=[
            TallyUnreachable("connection refused"),
            {"status": "success", "tally_voucher_guid": "g2"},
        ]
    )
    cache = IdempotencyCache(tmp_path / "connector.db")
    try:
        async with _serve(handler) as url:
            client = ConnectorWSClient(
                ws_url=url,
                connector_token="tok",
                company_id=company_id,
                tally=tally,
                heartbeat_seconds=999,
                initial_backoff=0.05,
                cache=cache,
            )
            await _drive(client, done)

        assert len(results) == 2
        assert results[0]["status"] == "error"
        assert results[0]["retryable"] is True
        assert results[1]["status"] == "success"
        # Re-executed because the retryable failure deleted the row.
        assert tally.post_voucher.call_count == 2  # type: ignore[attr-defined]
        # The successful second attempt is now cached.
        entry = cache.get("post_voucher", idem_key)
        assert entry is not None
        assert entry.status == "completed"
    finally:
        cache.close()
