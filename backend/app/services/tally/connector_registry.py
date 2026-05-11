"""In-memory registry of active connector WebSocket connections.

One entry per `company_id`. `send_command()` enqueues a `command`
envelope on the connector and returns a future that resolves to the
`command_result.payload` echoed back. The future is rejected on
timeout / disconnect.

Backend code (the voucher_dispatcher in P0.26, the status endpoint in
P0.25) only touches the *registry* — it never reaches into the WS
plumbing directly.

Phase 0 keeps this process-local. In Phase 1+ when the backend scales
horizontally, this becomes a Redis pub/sub fan-out keyed by company_id.
The contract here is designed so swapping the backing store doesn't
change call sites.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket


class ConnectorOffline(Exception):
    """Raised when a command targets a company whose connector isn't
    currently registered."""


class CommandTimeout(Exception):
    """Raised when a connector doesn't reply within `timeout_seconds`."""


@dataclass
class ConnectorConnection:
    """One live WebSocket. Owned by the registry."""

    company_id: UUID
    connector_id: UUID
    ws: WebSocket
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    tally_running: bool = True
    tally_version: str | None = None
    connector_version: str | None = None
    queued_outbound_count: int = 0

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send_envelope(
        self, *, type_: str, payload: dict[str, Any], request_id: UUID | None = None
    ) -> str:
        rid = str(request_id or uuid4())
        env = {
            "type": type_,
            "request_id": rid,
            "ts": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        await self.ws.send_text(json.dumps(env, separators=(",", ":")))
        return rid

    # ------------------------------------------------------------------
    # Command dispatch (futures)
    # ------------------------------------------------------------------

    async def send_command(
        self,
        *,
        command: str,
        args: dict[str, Any],
        timeout_seconds: int = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a `command` envelope and wait for the matching
        `command_result.payload`. Raises CommandTimeout on no reply.
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        rid = str(uuid4())
        self.pending[rid] = future
        try:
            payload = {
                "company_id": str(self.company_id),
                "command": command,
                "args": args,
                "timeout_seconds": timeout_seconds,
            }
            if idempotency_key:
                payload["idempotency_key"] = idempotency_key
            env = {
                "type": "command",
                "request_id": rid,
                "ts": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
            await self.ws.send_text(json.dumps(env, separators=(",", ":")))
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise CommandTimeout(
                f"connector did not respond to {command!r} in "
                f"{timeout_seconds}s"
            ) from exc
        finally:
            self.pending.pop(rid, None)

    def resolve_command_result(self, request_id: str, payload: dict[str, Any]) -> bool:
        """Called by the WS receive loop when a `command_result` arrives.

        Returns True if the request_id had a pending future, False if
        we no longer care about this reply (timed out or unsolicited).
        """
        future = self.pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    def cancel_pending(self) -> None:
        """Disconnect handler — cancel everyone waiting on us."""
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectorOffline("connector disconnected"))
        self.pending.clear()

    def touch_heartbeat(self) -> None:
        self.last_heartbeat_at = datetime.now(UTC)


class ConnectorRegistry:
    """Process-singleton: company_id → ConnectorConnection."""

    def __init__(self) -> None:
        self._by_company: dict[UUID, ConnectorConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, conn: ConnectorConnection) -> None:
        async with self._lock:
            existing = self._by_company.get(conn.company_id)
            if existing is not None:
                # Replace stale connection. Cancel pendings on the
                # old one so callers fail fast and reconnect logic
                # on their side kicks in.
                existing.cancel_pending()
                with contextlib.suppress(Exception):
                    await existing.ws.close(code=4429, reason="superseded")
            self._by_company[conn.company_id] = conn

    async def deregister(self, conn: ConnectorConnection) -> None:
        async with self._lock:
            current = self._by_company.get(conn.company_id)
            if current is conn:
                self._by_company.pop(conn.company_id, None)
                conn.cancel_pending()

    def get(self, company_id: UUID) -> ConnectorConnection | None:
        return self._by_company.get(company_id)

    def is_online(self, company_id: UUID) -> bool:
        conn = self._by_company.get(company_id)
        if conn is None:
            return False
        # Treat as offline if heartbeat is stale (>90s per protocol).
        age = (datetime.now(UTC) - conn.last_heartbeat_at).total_seconds()
        return age < 90.0

    async def send_command(
        self,
        *,
        company_id: UUID,
        command: str,
        args: dict[str, Any],
        timeout_seconds: int = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        conn = self._by_company.get(company_id)
        if conn is None:
            raise ConnectorOffline(
                f"no active connector for company {company_id}"
            )
        return await conn.send_command(
            command=command,
            args=args,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    def status_for(self, company_id: UUID) -> dict[str, Any] | None:
        """Snapshot for the `/connector/status` endpoint (P0.25)."""
        conn = self._by_company.get(company_id)
        if conn is None:
            return None
        return {
            "company_id": str(conn.company_id),
            "connected": True,
            "last_seen_at": conn.last_heartbeat_at.isoformat(),
            "tally_running": conn.tally_running,
            "tally_version": conn.tally_version,
            "connector_version": conn.connector_version,
            "queued_outbound_count": conn.queued_outbound_count,
        }


# ---------------------------------------------------------------------
# Module-singleton accessor
# ---------------------------------------------------------------------

_registry: ConnectorRegistry | None = None


def get_registry() -> ConnectorRegistry:
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry


# Convenience: monotonic clock the WS handler uses for heartbeat eviction.
def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
