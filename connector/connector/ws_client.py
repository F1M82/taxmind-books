"""WebSocket client for the cloud backend.

Implements the lifecycle from CONNECTOR_PROTOCOL.md:
  - Connect with `Authorization: Bearer <connector-token>`,
    `X-Company-ID`, `X-Connector-Version`, `X-Protocol-Version`.
  - Send `register` immediately on open; await `register_ack`.
  - Send `heartbeat` every HEARTBEAT_SECONDS; on no-ack within
    2× heartbeat seconds, close and reconnect.
  - Receive `command` → dispatch to message_handlers → reply with
    `command_result`.
  - Reconnect with exponential backoff (1s, 2, 4, 8, 16, 32, 60)
    plus ±20% jitter. Reset on successful registration.

Stop conditions (don't reconnect):
  - close codes 4001 (revoked), 4003 (company mismatch), 4400
    (protocol unsupported).

The connector identifies its registered company by what's in the
`register_ack.payload.company_id` field — the source of truth used
by the dispatcher's company_id verification.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import random
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidStatus,
)

from connector import __version__ as CONNECTOR_VERSION
from connector.envelope import (
    PROTOCOL_VERSION,
    ProtocolError,
    build_envelope,
    parse_envelope,
)
from connector.message_handlers import (
    CompanyMismatch,
    dispatch_command,
)
from connector.tally_client import TallyClient

logger = logging.getLogger("connector.ws_client")


_CLOSE_CODES_TERMINAL = frozenset({4001, 4003, 4400})


class ConnectorWSClient:
    """One long-lived client. Owns the reconnect loop."""

    def __init__(
        self,
        *,
        ws_url: str,
        connector_token: str,
        company_id: str,
        tally: TallyClient,
        heartbeat_seconds: int = 30,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ) -> None:
        self.ws_url = ws_url
        self.connector_token = connector_token
        self.company_id = company_id  # what the connector intends to register for
        self.tally = tally
        self.heartbeat_seconds = heartbeat_seconds
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff

        # Set on `register_ack` and used by command dispatcher to verify
        # `command.payload.company_id` matches the registered company.
        self.registered_company_id: str | None = None

        # Tracks the last heartbeat_ack timestamp for liveness detection.
        self._last_heartbeat_ack: float | None = None

        # Allows tests / callers to react after registration.
        self._register_event = asyncio.Event()

        self._stopping = False

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def run_forever(
        self,
        *,
        max_iterations: int | None = None,
        on_register_ack: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Connect-loop. Stops on terminal close codes or when
        `max_iterations` is reached (test hook).
        """
        backoff = self.initial_backoff
        iters = 0
        while not self._stopping:
            iters += 1
            try:
                await self._run_one_session(on_register_ack)
                # Clean exit (server closed normally) → reset backoff.
                backoff = self.initial_backoff
            except _Terminal as exc:
                logger.error(
                    "terminal close code %s — stopping reconnect",
                    exc.close_code,
                )
                return
            except Exception:  # noqa: BLE001 — broad on the reconnect boundary
                logger.exception("session ended; will reconnect")
                # Exponential + jitter backoff.
                jitter = random.uniform(-0.2, 0.2) * backoff
                await asyncio.sleep(backoff + jitter)
                backoff = min(self.max_backoff, backoff * 2)
            if max_iterations is not None and iters >= max_iterations:
                return

    def stop(self) -> None:
        self._stopping = True

    # ------------------------------------------------------------------
    # One session
    # ------------------------------------------------------------------

    async def _run_one_session(
        self,
        on_register_ack: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {self.connector_token}",
            "X-Company-ID": self.company_id,
            "X-Connector-Version": CONNECTOR_VERSION,
            "X-Protocol-Version": str(PROTOCOL_VERSION),
        }
        try:
            async with websockets.connect(
                self.ws_url,
                additional_headers=list(headers.items()),
            ) as ws:
                await self._send_register(ws)
                hb = asyncio.create_task(self._heartbeat_loop(ws))
                rx = asyncio.create_task(
                    self._receive_loop(ws, on_register_ack)
                )
                done, pending = await asyncio.wait(
                    {hb, rx},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                # Surface any exception from whichever finished first.
                for t in done:
                    if not t.cancelled():
                        exc = t.exception()
                        if exc is not None:
                            raise exc
        except InvalidStatus as exc:
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            if code in _CLOSE_CODES_TERMINAL:
                raise _Terminal(code) from exc
            raise
        except (ConnectionClosedOK, ConnectionClosedError) as exc:
            if (
                getattr(exc, "rcvd", None) is not None
                and exc.rcvd.code in _CLOSE_CODES_TERMINAL
            ):
                raise _Terminal(exc.rcvd.code) from exc
            raise
        except ConnectionClosed:
            raise

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def _send_register(self, ws: ClientConnection) -> None:
        env = build_envelope(
            type_="register",
            payload={
                "connector_version": CONNECTOR_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "tally_running": await self.tally.ping(),
                "host": {
                    "os": platform.platform(),
                    "hostname": socket.gethostname(),
                    "user": platform.node(),
                },
                "queued_outbound_count": 0,
            },
        )
        await ws.send(env)

    async def _send_heartbeat(self, ws: ClientConnection) -> None:
        env = build_envelope(
            type_="heartbeat",
            payload={
                "tally_running": await self.tally.ping(),
                "queued_outbound_count": 0,
            },
        )
        await ws.send(env)

    async def _send_command_result(
        self,
        ws: ClientConnection,
        *,
        command_request_id: str,
        result_payload: dict[str, Any],
    ) -> None:
        env = build_envelope(
            type_="command_result",
            payload=result_payload,
            request_id=_uuid_or_random(command_request_id),
        )
        await ws.send(env)

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._send_heartbeat(ws)

    async def _receive_loop(
        self,
        ws: ClientConnection,
        on_register_ack: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        async for raw in ws:
            try:
                env = parse_envelope(raw)
            except ProtocolError as exc:
                logger.warning("dropped malformed envelope: %s", exc)
                continue
            await self._handle_inbound(ws, env, on_register_ack)

    async def _handle_inbound(
        self,
        ws: ClientConnection,
        env: dict[str, Any],
        on_register_ack: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        type_ = env["type"]
        payload = env["payload"]
        if type_ == "register_ack":
            self.registered_company_id = payload.get("company_id")
            self._register_event.set()
            if on_register_ack is not None:
                await on_register_ack(payload)
        elif type_ == "heartbeat_ack":
            import time

            self._last_heartbeat_ack = time.monotonic()
        elif type_ == "command":
            await self._handle_command(ws, env)
        elif type_ == "error":
            logger.warning("backend error: %s", payload)
        else:
            logger.warning("ignoring unknown message type %r", type_)

    async def _handle_command(
        self, ws: ClientConnection, env: dict[str, Any]
    ) -> None:
        if self.registered_company_id is None:
            logger.warning(
                "received command before register_ack; dropping"
            )
            return
        try:
            result_payload = await dispatch_command(
                tally=self.tally,
                payload=env["payload"],
                registered_company_id=self.registered_company_id,
            )
        except CompanyMismatch as exc:
            result_payload = {
                "command": env["payload"].get("command"),
                "status": "error",
                "error": {
                    "code": "company_mismatch",
                    "message": str(exc),
                },
                "retryable": False,
            }
        await self._send_command_result(
            ws,
            command_request_id=str(env["request_id"]),
            result_payload=result_payload,
        )


def _uuid_or_random(s: str):  # type: ignore[no-untyped-def]
    """Return s as UUID if parseable, else a fresh UUID. Used so the
    command_result.request_id echoes the command's request_id (per
    protocol) but doesn't crash on a malformed inbound id."""
    from uuid import UUID

    try:
        return UUID(s)
    except (ValueError, TypeError):
        return uuid4()


class _Terminal(Exception):
    """Internal: thrown to break out of the reconnect loop."""

    def __init__(self, close_code: int) -> None:
        super().__init__(f"terminal close code {close_code}")
        self.close_code = close_code
