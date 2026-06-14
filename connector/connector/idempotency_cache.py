"""SQLite-backed command idempotency cache.

Implements the connector-side dedup store specified in
CONNECTOR_PROTOCOL.md §"command" and designed in
docs/connector_idempotency_design.md.

One row per ``(command, idempotency_key)``. Mutating commands
(``post_voucher`` and, later, ``approve``/``reject_optional_voucher``)
record intent *before* touching Tally (``record_in_flight``), then settle
to a terminal state (``record_completed`` / ``record_failed``) or are
cleared for a fresh retry (``delete_for_retry``). A replay whose key is
already ``completed``/``failed`` returns the cached result without
re-executing against Tally.

This module is intentionally synchronous — ``sqlite3`` is sync. The async
caller (``dispatch_command``, session 2) wraps each method in
``asyncio.to_thread(...)``. A ``threading.Lock`` guards the shared
connection so it is safe across the thread-pool workers ``to_thread``
uses.

Persistence lives at the platform AppData / XDG_DATA root
(``%APPDATA%\\TaxMindBooks\\connector.db`` on Windows,
``~/.local/share/taxmindbooks/connector.db`` elsewhere) in WAL mode so an
abrupt process exit cannot corrupt an already-committed row.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

logger = logging.getLogger("connector.idempotency_cache")

SCHEMA_VERSION = 1

STATUS_IN_FLIGHT = "in_flight"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Settled rows older than this are swept by `cleanup_stale`. Long enough
# to cover any plausible backend retry window, short enough to keep the
# on-disk cache bounded. See docs/connector_idempotency_design.md.
DEFAULT_TTL_DAYS = 30


@dataclass(frozen=True)
class CacheEntry:
    """One row of the idempotency cache (``result_payload`` decoded)."""

    command: str
    idempotency_key: str
    request_payload_hash: str
    status: str
    result_payload: dict[str, Any] | None
    created_at: str
    updated_at: str


def default_db_path() -> Path:
    """Resolve the platform-appropriate connector database path.

    Windows: ``%APPDATA%\\TaxMindBooks\\connector.db`` (falls back to
    ``~/AppData/Roaming`` when APPDATA is unset). Other OSes:
    ``$XDG_DATA_HOME/taxmindbooks/connector.db`` (falls back to
    ``~/.local/share``).
    """
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return root / "TaxMindBooks" / "connector.db"
    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / "taxmindbooks" / "connector.db"


def hash_request_payload(payload: dict[str, Any]) -> str:
    """Stable sha256 hex of a command's args, for key-reuse detection.

    Canonicalised with sorted keys + compact separators so logically
    identical payloads hash identically regardless of dict ordering.
    """
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class IdempotencyCache:
    """Synchronous SQLite store for command idempotency state."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path) if db_path is not None else default_db_path()
        )
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: the async caller dispatches each method
        # via asyncio.to_thread, which runs on pool threads. Concurrency is
        # serialised by self._lock, so cross-thread use of the one
        # connection is safe.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        """Create the schema if absent. Idempotent — safe on every boot.

        WAL + synchronous=FULL are set before any DML so they run in
        autocommit (journal_mode cannot change inside a transaction). A
        ``user_version`` anchor leaves room for a future migration ladder
        without Alembic-grade machinery.
        """
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_idempotency (
                    command              TEXT NOT NULL,
                    idempotency_key      TEXT NOT NULL,
                    request_payload_hash TEXT NOT NULL,
                    status               TEXT NOT NULL,
                    result_payload       TEXT,
                    created_at           TEXT NOT NULL,
                    updated_at           TEXT NOT NULL,
                    PRIMARY KEY (command, idempotency_key)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cmd_idem_created "
                "ON command_idempotency (created_at)"
            )
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> IdempotencyCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, command: str, idempotency_key: str) -> CacheEntry | None:
        """Return the cached entry for ``(command, key)`` or ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT command, idempotency_key, request_payload_hash, "
                "status, result_payload, created_at, updated_at "
                "FROM command_idempotency "
                "WHERE command = ? AND idempotency_key = ?",
                (command, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        raw = row["result_payload"]
        return CacheEntry(
            command=row["command"],
            idempotency_key=row["idempotency_key"],
            request_payload_hash=row["request_payload_hash"],
            status=row["status"],
            result_payload=json.loads(raw) if raw is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_in_flight(
        self, command: str, idempotency_key: str, request_payload_hash: str
    ) -> None:
        """Record posting intent before the Tally call.

        Plain INSERT: a primary-key conflict raises
        ``sqlite3.IntegrityError`` on purpose — a second in-flight insert
        for the same key means a concurrent or mis-sequenced attempt, and
        the caller must back off rather than double-execute.
        """
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO command_idempotency ("
                "command, idempotency_key, request_payload_hash, status, "
                "result_payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (
                    command,
                    idempotency_key,
                    request_payload_hash,
                    STATUS_IN_FLIGHT,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def record_completed(
        self,
        command: str,
        idempotency_key: str,
        result_payload: dict[str, Any],
    ) -> None:
        """Settle a key to ``completed`` with its cached result.

        Upsert: updates an existing in-flight row (preserving its original
        ``request_payload_hash`` / ``created_at``) or inserts a fresh
        terminal row if none exists.
        """
        self._settle(
            command, idempotency_key, STATUS_COMPLETED, result_payload
        )

    def record_failed(
        self,
        command: str,
        idempotency_key: str,
        error_payload: dict[str, Any],
    ) -> None:
        """Settle a key to ``failed`` with its cached error envelope.

        Used only for non-retryable failures; retryable ones call
        ``delete_for_retry`` so the next attempt re-executes.
        """
        self._settle(command, idempotency_key, STATUS_FAILED, error_payload)

    def _settle(
        self,
        command: str,
        idempotency_key: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now_iso()
        blob = json.dumps(payload, separators=(",", ":"))
        with self._lock:
            # ON CONFLICT preserves the original request_payload_hash and
            # created_at from the in-flight row; only the status, payload,
            # and updated_at advance.
            self._conn.execute(
                "INSERT INTO command_idempotency ("
                "command, idempotency_key, request_payload_hash, status, "
                "result_payload, created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?, ?, ?) "
                "ON CONFLICT(command, idempotency_key) DO UPDATE SET "
                "status = excluded.status, "
                "result_payload = excluded.result_payload, "
                "updated_at = excluded.updated_at",
                (command, idempotency_key, status, blob, now, now),
            )
            self._conn.commit()

    def delete_for_retry(self, command: str, idempotency_key: str) -> None:
        """Remove a row so the next attempt re-executes against Tally.

        Used on retryable failures, where no terminal state should be
        cached.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM command_idempotency "
                "WHERE command = ? AND idempotency_key = ?",
                (command, idempotency_key),
            )
            self._conn.commit()

    def cleanup_stale(self, *, older_than_days: int = DEFAULT_TTL_DAYS) -> int:
        """Best-effort removal of settled rows older than the TTL.

        Deletes ``completed`` / ``failed`` rows whose ``created_at`` is
        older than the cutoff and returns the number removed.
        ``in_flight`` rows are NEVER touched — they are crash-recovery
        sentinels (OPEN-A), not stale data.

        Best-effort by contract: any failure is logged at WARN and
        swallowed (returns 0). An unbounded-growing cache is
        bounded-bad; a connector that crashes over housekeeping is
        unbounded-bad. See docs/connector_idempotency_design.md
        §"Cleanup policy".
        """
        cutoff = (
            datetime.now(UTC) - timedelta(days=older_than_days)
        ).isoformat()
        try:
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM command_idempotency "
                    "WHERE status IN (?, ?) AND created_at < ?",
                    (STATUS_COMPLETED, STATUS_FAILED, cutoff),
                )
                self._conn.commit()
                return cur.rowcount
        except Exception:
            logger.warning(
                "idempotency cache cleanup failed "
                "(older_than_days=%s); leaving rows in place",
                older_than_days,
                exc_info=True,
            )
            return 0
