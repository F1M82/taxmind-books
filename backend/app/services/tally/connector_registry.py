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


class TallyRetryableEnvelope(Exception):
    """Raised by voucher_dispatcher when the connector returned an
    envelope with status="error" and retryable=True.

    Sibling of ConnectorOffline/CommandTimeout for the future Celery
    worker mode's `autoretry_for` tuple (Phase 0.5+ once BUG-Books-003
    is resolved). Phase 0 eager mode catches it in `_drive`'s tuple
    and commits the audit row before swallowing.

    Carries the envelope's error code and message for the audit row.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message


class TallyRejectedEnvelope(Exception):
    """Raised by voucher_dispatcher when the connector returned an
    envelope with status="error" and retryable=False.

    Intentionally NOT in Celery's `autoretry_for` — Tally rejected
    the operation for a reason that needs operator action (wrong
    company loaded, missing ledger, malformed voucher). Phase 0 eager
    mode catches it in `_drive`'s tuple, commits the audit row, then
    swallows; no retry.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message


@dataclass
class ConnectorConnection:
    """One live WebSocket. Owned by the registry."""

    company_id: UUID
    connector_id: UUID
    ws: WebSocket
    authorized_company_ids: set[UUID] = field(default_factory=set)
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
    connector_build_sha: str | None = None
    connector_built_at: str | None = None
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
        company_id: UUID | None = None,
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
                "company_id": str(company_id or self.company_id),
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
    """Process-singleton registry indexed by connector and company.

    The connector index is authoritative for mapped P3.8 routing. The company
    index is deliberately retained for legacy company-bound callers.
    """

    def __init__(self) -> None:
        self._by_company: dict[UUID, ConnectorConnection] = {}
        self._by_connector: dict[UUID, ConnectorConnection] = {}
        self._lock = asyncio.Lock()

    async def register(  # audit-exempt: in-memory operational connection state
        self, conn: ConnectorConnection, company_ids: set[UUID] | None = None
    ) -> None:
        async with self._lock:
            if company_ids:
                conn.authorized_company_ids.update(company_ids)
            conn.authorized_company_ids.add(conn.company_id)
            existing = self._by_connector.get(conn.connector_id)
            if existing is not None:
                # Replace stale connection. Cancel pendings on the
                # old one so callers fail fast and reconnect logic
                # on their side kicks in.
                existing.cancel_pending()
                with contextlib.suppress(Exception):
                    await existing.ws.close(code=4429, reason="superseded")
                self._remove_indexes(existing)
            self._by_connector[conn.connector_id] = conn
            for company_id in conn.authorized_company_ids:
                self._by_company[company_id] = conn

    async def deregister(self, conn: ConnectorConnection) -> None:
        async with self._lock:
            current = self._by_connector.get(conn.connector_id)
            if current is conn:
                self._remove_indexes(conn)
                conn.cancel_pending()

    def _remove_indexes(self, conn: ConnectorConnection) -> None:
        self._by_connector.pop(conn.connector_id, None)
        for company_id in conn.authorized_company_ids:
            if self._by_company.get(company_id) is conn:
                self._by_company.pop(company_id, None)

    def get(self, company_id: UUID) -> ConnectorConnection | None:
        return self._by_company.get(company_id)

    def get_by_connector(self, connector_id: UUID) -> ConnectorConnection | None:
        return self._by_connector.get(connector_id)

    def is_online(
        self, company_id: UUID | None = None, *, connector_id: UUID | None = None
    ) -> bool:
        if connector_id is None and company_id is None:
            return False
        if connector_id is not None:
            conn = self._by_connector.get(connector_id)
        else:
            assert company_id is not None
            conn = self._by_company.get(company_id)
        if conn is None:
            return False
        # Treat as offline if heartbeat is stale (>90s per protocol).
        age = (datetime.now(UTC) - conn.last_heartbeat_at).total_seconds()
        return age < 90.0

    async def send_command(
        self,
        *,
        company_id: UUID | None = None,
        connector_id: UUID | None = None,
        command: str,
        args: dict[str, Any],
        timeout_seconds: int = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        conn = (
            self._by_connector.get(connector_id)
            if connector_id is not None
            else self._by_company.get(company_id) if company_id is not None else None
        )
        if conn is None:
            raise ConnectorOffline(
                f"no active connector for {connector_id or company_id}"
            )
        return await conn.send_command(
            command=command,
            args=args,
            company_id=company_id,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
        )

    def status_for(self, company_id: UUID) -> dict[str, Any] | None:
        """Snapshot for the `/connector/status` endpoint (P0.25)."""
        conn = self._by_company.get(company_id)
        if conn is None:
            return None
        return {
            "company_id": str(company_id),
            "connector_id": str(conn.connector_id),
            "connected": True,
            "last_seen_at": conn.last_heartbeat_at.isoformat(),
            "tally_running": conn.tally_running,
            "tally_version": conn.tally_version,
            "connector_version": conn.connector_version,
            "connector_build_sha": conn.connector_build_sha,
            "connector_built_at": conn.connector_built_at,
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
