"""Unit tests for idempotency_cache.py (BUG-003 prep, session 1).

The cache is exercised in isolation here — not yet wired into
dispatch_command. Every test uses its own temp-file database (via the
``cache`` fixture / ``tmp_path``) so there is no cross-test pollution.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from connector.idempotency_cache import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_FLIGHT,
    IdempotencyCache,
    default_db_path,
    hash_request_payload,
)


def _backdate(
    cache: IdempotencyCache, command: str, key: str, *, days: int
) -> None:
    """Rewrite a row's created_at to `days` in the past (test helper)."""
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    cache._conn.execute(
        "UPDATE command_idempotency SET created_at = ? "
        "WHERE command = ? AND idempotency_key = ?",
        (old, command, key),
    )
    cache._conn.commit()


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[IdempotencyCache]:
    c = IdempotencyCache(tmp_path / "connector.db")
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "connector.db"
    first = IdempotencyCache(path)
    first.close()
    # Re-opening runs CREATE TABLE IF NOT EXISTS again — must be a no-op.
    second = IdempotencyCache(path)
    try:
        assert second.get("post_voucher", "anything") is None
    finally:
        second.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    path = tmp_path / "connector.db"
    IdempotencyCache(path).close()
    # WAL is recorded in the database header; a fresh connection reports it.
    conn = sqlite3.connect(str(path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_user_version_set(tmp_path: Path) -> None:
    path = tmp_path / "connector.db"
    IdempotencyCache(path).close()
    conn = sqlite3.connect(str(path))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 1


# ---------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------


def test_get_unknown_returns_none(cache: IdempotencyCache) -> None:
    assert cache.get("post_voucher", "never-seen") is None


def test_record_in_flight_then_get(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "v1", "hash-abc")
    entry = cache.get("post_voucher", "v1")
    assert entry is not None
    assert entry.status == STATUS_IN_FLIGHT
    assert entry.request_payload_hash == "hash-abc"
    assert entry.result_payload is None
    assert entry.created_at == entry.updated_at


def test_record_completed_then_get(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    result = {"status": "success", "tally_voucher_guid": None}
    cache.record_completed("post_voucher", "v1", result)
    entry = cache.get("post_voucher", "v1")
    assert entry is not None
    assert entry.status == STATUS_COMPLETED
    assert entry.result_payload == result


def test_record_failed_then_get(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    error = {"code": "tally_validation_failed", "message": "Ledger missing"}
    cache.record_failed("post_voucher", "v1", error)
    entry = cache.get("post_voucher", "v1")
    assert entry is not None
    assert entry.status == STATUS_FAILED
    assert entry.result_payload == error


def test_delete_for_retry_then_get_returns_none(
    cache: IdempotencyCache,
) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    cache.delete_for_retry("post_voucher", "v1")
    assert cache.get("post_voucher", "v1") is None


def test_settle_preserves_hash_and_created_at(
    cache: IdempotencyCache,
) -> None:
    cache.record_in_flight("post_voucher", "v1", "orig-hash")
    before = cache.get("post_voucher", "v1")
    assert before is not None
    cache.record_completed("post_voucher", "v1", {"ok": True})
    after = cache.get("post_voucher", "v1")
    assert after is not None
    # The upsert advances status/payload but keeps the original
    # in-flight hash and created_at.
    assert after.request_payload_hash == "orig-hash"
    assert after.created_at == before.created_at
    assert after.status == STATUS_COMPLETED


def test_record_in_flight_duplicate_raises(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    with pytest.raises(sqlite3.IntegrityError):
        cache.record_in_flight("post_voucher", "v1", "h")


def test_entries_scoped_by_command_and_key(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    # A different key, or the same key under a different command, is
    # a distinct entry.
    assert cache.get("post_voucher", "v2") is None
    assert cache.get("approve_optional_voucher", "v1") is None


def test_completed_then_delete_clears_for_reexecution(
    cache: IdempotencyCache,
) -> None:
    cache.record_in_flight("post_voucher", "v1", "h")
    cache.record_completed("post_voucher", "v1", {"ok": True})
    cache.delete_for_retry("post_voucher", "v1")
    assert cache.get("post_voucher", "v1") is None


# ---------------------------------------------------------------------
# Helpers: hashing + path resolution
# ---------------------------------------------------------------------


def test_hash_request_payload_is_order_independent() -> None:
    a = hash_request_payload({"x": 1, "y": 2})
    b = hash_request_payload({"y": 2, "x": 1})
    assert a == b
    assert a != hash_request_payload({"x": 1, "y": 3})


def test_default_db_path_platform_appropriate() -> None:
    path = default_db_path()
    assert path.name == "connector.db"
    if sys.platform.startswith("win"):
        assert path.parent.name == "TaxMindBooks"
    else:
        assert path.parent.name == "taxmindbooks"


def test_default_db_path_honours_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if sys.platform.startswith("win"):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert default_db_path() == tmp_path / "TaxMindBooks" / "connector.db"
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert default_db_path() == tmp_path / "taxmindbooks" / "connector.db"


def test_db_file_created_on_init(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "connector.db"
    cache = IdempotencyCache(path)
    try:
        assert path.exists()
    finally:
        cache.close()


# ---------------------------------------------------------------------
# Cleanup policy (TTL)
# ---------------------------------------------------------------------


def test_cleanup_preserves_recent_rows(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "fresh", "h")
    cache.record_completed("post_voucher", "fresh", {"status": "success"})
    removed = cache.cleanup_stale(older_than_days=30)
    assert removed == 0
    assert cache.get("post_voucher", "fresh") is not None


def test_cleanup_removes_settled_rows_older_than_ttl(
    cache: IdempotencyCache,
) -> None:
    cache.record_in_flight("post_voucher", "old-ok", "h")
    cache.record_completed("post_voucher", "old-ok", {"status": "success"})
    cache.record_in_flight("post_voucher", "old-bad", "h")
    cache.record_failed("post_voucher", "old-bad", {"code": "rejected"})
    _backdate(cache, "post_voucher", "old-ok", days=40)
    _backdate(cache, "post_voucher", "old-bad", days=40)

    removed = cache.cleanup_stale(older_than_days=30)
    assert removed == 2
    assert cache.get("post_voucher", "old-ok") is None
    assert cache.get("post_voucher", "old-bad") is None


def test_cleanup_never_removes_in_flight(cache: IdempotencyCache) -> None:
    cache.record_in_flight("post_voucher", "stuck", "h")
    # Even a very old in_flight row is a crash-recovery sentinel (OPEN-A),
    # never stale data — cleanup must leave it.
    _backdate(cache, "post_voucher", "stuck", days=100)
    removed = cache.cleanup_stale(older_than_days=30)
    assert removed == 0
    entry = cache.get("post_voucher", "stuck")
    assert entry is not None
    assert entry.status == STATUS_IN_FLIGHT


def test_cleanup_failure_is_swallowed_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = IdempotencyCache(tmp_path / "connector.db")
    # Closing the connection makes the next execute raise — stands in for
    # any DB-level failure during cleanup.
    cache.close()
    with caplog.at_level(
        logging.WARNING, logger="connector.idempotency_cache"
    ):
        result = cache.cleanup_stale(older_than_days=30)
    assert result == 0  # swallowed, no exception propagated
    assert any(
        "cleanup failed" in rec.getMessage() for rec in caplog.records
    ), "expected a WARN on cleanup failure"
