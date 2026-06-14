"""Integration tests for dispatch_command + idempotency cache (session 2).

These exercise the cache wiring in `dispatch_command`: replay of a settled
key must NOT re-invoke the Tally client. A hand-rolled `_FakeTally` records
how many times `post_voucher` is called, so "no second Tally call on
replay" is a hard assertion rather than a mock convention.

The pre-existing dispatch tests (cache omitted) remain valid and untouched —
`cache=None` preserves the original behaviour exactly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from connector.idempotency_cache import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    IdempotencyCache,
    hash_request_payload,
)
from connector.message_handlers import dispatch_command
from connector.tally_client import TallyImportRejected, TallyUnreachable

COMPANY = "11111111-1111-1111-1111-111111111111"


def _pv_args() -> dict[str, Any]:
    return {
        "voucher_type": "Receipt",
        "date": "2026-05-08",
        "voucher_number": "R-1",
        "party_name": "Sharma",
        "narration": "Payment received",
        "entries": [
            {"ledger_name": "Bank", "amount": "1000.00", "entry_type": "Dr"},
            {"ledger_name": "Sharma", "amount": "1000.00", "entry_type": "Cr"},
        ],
    }


def _pv_payload(key: str = "v-1") -> dict[str, Any]:
    return {
        "company_id": COMPANY,
        "command": "post_voucher",
        "args": _pv_args(),
        "idempotency_key": key,
    }


class _FakeTally:
    """Stands in for TallyClient; counts post_voucher invocations."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._result = result or {
            "status": "success",
            "tally_voucher_guid": None,
        }
        self._raises = raises
        self.post_voucher_calls = 0
        self.ledger_calls = 0

    async def post_voucher(self, voucher: Any) -> dict[str, Any]:
        self.post_voucher_calls += 1
        if self._raises is not None:
            raise self._raises
        return self._result

    async def get_all_ledgers(self) -> list[Any]:
        self.ledger_calls += 1
        return []

    async def get_all_groups(self) -> list[Any]:
        return []


@pytest.fixture
def cache(tmp_path: Path) -> Iterator[IdempotencyCache]:
    c = IdempotencyCache(tmp_path / "connector.db")
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------
# Replay returns cached result without re-posting
# ---------------------------------------------------------------------


async def test_first_seen_success_then_replay_returns_cached(
    cache: IdempotencyCache,
) -> None:
    tally = _FakeTally(
        result={"status": "success", "tally_voucher_guid": "g1"}
    )
    first = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-1"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert first["status"] == "success"
    assert tally.post_voucher_calls == 1
    entry = cache.get("post_voucher", "v-1")
    assert entry is not None
    assert entry.status == STATUS_COMPLETED

    # Replay: same key — must return the cached envelope, NOT call Tally.
    second = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-1"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert tally.post_voucher_calls == 1  # unchanged → no re-post
    assert second["status"] == "success"
    assert second["result"]["tally_voucher_guid"] == "g1"


async def test_rejected_then_replay_returns_cached_error(
    cache: IdempotencyCache,
) -> None:
    tally = _FakeTally(
        raises=TallyImportRejected("Ledger 'Sales' does not exist", 1, "<r/>")
    )
    first = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-2"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert first["status"] == "error"
    assert first["retryable"] is False
    assert first["error"]["code"] == "TallyImportRejected"
    assert tally.post_voucher_calls == 1
    entry = cache.get("post_voucher", "v-2")
    assert entry is not None
    assert entry.status == STATUS_FAILED

    # Replay: non-retryable failure is cached — no re-post.
    second = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-2"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert tally.post_voucher_calls == 1
    assert second["status"] == "error"
    assert second["error"]["code"] == "TallyImportRejected"


async def test_retryable_error_deletes_and_next_is_first_seen(
    cache: IdempotencyCache,
) -> None:
    tally = _FakeTally(raises=TallyUnreachable("connection refused"))
    first = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-3"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert first["status"] == "error"
    assert first["retryable"] is True
    assert tally.post_voucher_calls == 1
    # Retryable → row deleted so a retry re-executes against Tally.
    assert cache.get("post_voucher", "v-3") is None

    # Second attempt now succeeds and is treated as first-seen.
    tally2 = _FakeTally(
        result={"status": "success", "tally_voucher_guid": "g3"}
    )
    second = await dispatch_command(
        tally=tally2,  # type: ignore[arg-type]
        payload=_pv_payload("v-3"),
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert tally2.post_voucher_calls == 1
    assert second["status"] == "success"
    entry = cache.get("post_voucher", "v-3")
    assert entry is not None
    assert entry.status == STATUS_COMPLETED


# ---------------------------------------------------------------------
# Crash-window (in_flight) handling — OPEN-A
# ---------------------------------------------------------------------


async def test_in_flight_row_warns_and_proceeds_as_first_seen(
    cache: IdempotencyCache, caplog: pytest.LogCaptureFixture
) -> None:
    # Simulate a crash: intent recorded, never settled.
    cache.record_in_flight(
        "post_voucher", "v-4", hash_request_payload(_pv_args())
    )
    tally = _FakeTally(
        result={"status": "success", "tally_voucher_guid": "g4"}
    )
    with caplog.at_level(
        logging.WARNING, logger="connector.message_handlers"
    ):
        result = await dispatch_command(
            tally=tally,  # type: ignore[arg-type]
            payload=_pv_payload("v-4"),
            registered_company_id=COMPANY,
            cache=cache,
        )
    # Proceeded as first-seen (no IntegrityError from a re-insert) and
    # settled to completed.
    assert tally.post_voucher_calls == 1
    assert result["status"] == "success"
    entry = cache.get("post_voucher", "v-4")
    assert entry is not None
    assert entry.status == STATUS_COMPLETED
    assert any(
        "OPEN-A" in rec.getMessage() for rec in caplog.records
    ), "expected an OPEN-A crash-window WARN"


# ---------------------------------------------------------------------
# Bypass paths
# ---------------------------------------------------------------------


async def test_non_mutating_command_bypasses_cache(
    cache: IdempotencyCache,
) -> None:
    tally = _FakeTally()
    payload = {
        "company_id": COMPANY,
        "command": "sync_masters",
        "args": {},
        "idempotency_key": "sm-1",
    }
    result = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=payload,
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert result["status"] == "success"
    assert tally.ledger_calls == 1
    # Nothing was written to the cache for a non-mutating command.
    assert cache.get("sync_masters", "sm-1") is None


async def test_mutating_without_key_bypasses_cache(
    cache: IdempotencyCache,
) -> None:
    tally = _FakeTally()
    payload = {
        "company_id": COMPANY,
        "command": "post_voucher",
        "args": _pv_args(),
        # no idempotency_key
    }
    result = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=payload,
        registered_company_id=COMPANY,
        cache=cache,
    )
    assert result["status"] == "success"
    assert tally.post_voucher_calls == 1


async def test_no_cache_executes_directly(cache: IdempotencyCache) -> None:
    # cache omitted entirely — original behaviour, unchanged.
    tally = _FakeTally(
        result={"status": "success", "tally_voucher_guid": "g"}
    )
    result = await dispatch_command(
        tally=tally,  # type: ignore[arg-type]
        payload=_pv_payload("v-9"),
        registered_company_id=COMPANY,
    )
    assert result["status"] == "success"
    assert tally.post_voucher_calls == 1


# ---------------------------------------------------------------------
# Hash determinism
# ---------------------------------------------------------------------


def test_request_hash_deterministic_across_runs() -> None:
    assert hash_request_payload(_pv_args()) == hash_request_payload(_pv_args())
