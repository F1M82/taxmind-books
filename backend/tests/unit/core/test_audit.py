"""Unit tests for app.core.audit.

These tests use bare model instances (no DB) — the AuditEmitter
appends to a fake session that captures `.add()` calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.audit import (
    _ALLOWED_ACTIONS,
    AuditContext,
    AuditEmitter,
    _compute_diff,
    _normalize_for_json,
)


# ---------------- Stubs ----------------


@dataclass
class _StubUser:
    id: UUID


@dataclass
class _StubCompany:
    id: UUID


class _FakeSession:
    """A minimal SQLAlchemy-like Session that records `.add()` calls."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, instance: Any) -> None:
        self.added.append(instance)


def _make_ctx(*, user: bool = True, source: str = "api") -> AuditContext:
    company = _StubCompany(id=uuid4())
    user_obj = _StubUser(id=uuid4()) if user else None
    return AuditContext(
        company=company,  # type: ignore[arg-type]
        user=user_obj,  # type: ignore[arg-type]
        ip_address="127.0.0.1",
        user_agent="pytest/1.0",
        request_id=uuid4(),
        source=source,
    )


# ---------------- AuditContext ----------------


def test_audit_context_rejects_unknown_source() -> None:
    with pytest.raises(ValueError) as exc:
        AuditContext(
            company=_StubCompany(id=uuid4()),  # type: ignore[arg-type]
            user=None,
            ip_address=None,
            user_agent=None,
            request_id=uuid4(),
            source="invalid",
        )
    assert "source" in str(exc.value)


def test_audit_context_accepts_all_valid_sources() -> None:
    for s in ("api", "worker", "connector", "system"):
        AuditContext(
            company=_StubCompany(id=uuid4()),  # type: ignore[arg-type]
            user=None,
            ip_address=None,
            user_agent=None,
            request_id=uuid4(),
            source=s,
        )


# ---------------- AuditEmitter.emit ----------------


def test_emit_writes_row_with_right_shape() -> None:
    db = _FakeSession()
    ctx = _make_ctx()
    emitter = AuditEmitter(db, ctx)  # type: ignore[arg-type]

    voucher_id = uuid4()
    log = emitter.emit(
        action="voucher.created",
        entity_type="voucher",
        entity_id=voucher_id,
        old_value=None,
        new_value={"total_amount": Decimal("123.45"), "narration": "test"},
    )

    assert len(db.added) == 1
    assert db.added[0] is log
    assert log.action == "voucher.created"
    assert log.entity_type == "voucher"
    assert log.entity_id == voucher_id
    assert log.user_id == ctx.user.id  # type: ignore[union-attr]
    assert log.company_id == ctx.company.id
    assert log.source == "api"
    assert log.request_id == ctx.request_id
    assert log.ip_address == "127.0.0.1"
    assert log.old_value is None
    assert log.new_value == {"total_amount": "123.45", "narration": "test"}


def test_emit_does_not_commit() -> None:
    """Emitter must never call db.commit() — caller owns the txn."""
    db = _FakeSession()
    db.commit = lambda: pytest.fail("emitter should not commit")  # type: ignore[method-assign]
    emitter = AuditEmitter(db, _make_ctx())  # type: ignore[arg-type]
    emitter.emit(
        action="voucher.created",
        entity_type="voucher",
        entity_id=uuid4(),
        old_value=None,
        new_value={"x": 1},
    )


def test_emit_rejects_unknown_action() -> None:
    db = _FakeSession()
    emitter = AuditEmitter(db, _make_ctx())  # type: ignore[arg-type]
    with pytest.raises(ValueError) as exc:
        emitter.emit(
            action="not.an.action",
            entity_type="voucher",
            entity_id=uuid4(),
            old_value=None,
            new_value=None,
        )
    assert "unknown audit action" in str(exc.value)


def test_emit_with_no_user_writes_null_user_id() -> None:
    """System events have ctx.user = None → user_id null."""
    db = _FakeSession()
    emitter = AuditEmitter(db, _make_ctx(user=False, source="system"))  # type: ignore[arg-type]
    log = emitter.emit(
        action="company.created",
        entity_type="company",
        entity_id=uuid4(),
        old_value=None,
        new_value={"name": "Acme"},
    )
    assert log.user_id is None


def test_emit_computes_diff_for_update() -> None:
    db = _FakeSession()
    emitter = AuditEmitter(db, _make_ctx())  # type: ignore[arg-type]
    log = emitter.emit(
        action="voucher.updated",
        entity_type="voucher",
        entity_id=uuid4(),
        old_value={"narration": "old", "amount": Decimal("100.00")},
        new_value={"narration": "new", "amount": Decimal("100.00")},
    )
    assert log.changes == {"narration": ["old", "new"]}


def test_allowed_actions_contains_v0_and_v1_2() -> None:
    """Spot-check a few critical action names from AUDIT.md."""
    assert "voucher.created" in _ALLOWED_ACTIONS
    assert "voucher.posted_to_tally" in _ALLOWED_ACTIONS
    assert "voucher.approved_to_regular" in _ALLOWED_ACTIONS
    assert "user.password_changed" in _ALLOWED_ACTIONS
    assert "account.deletion_requested" in _ALLOWED_ACTIONS


# ---------------- _normalize_for_json ----------------


class _Status(PyEnum):
    posted = "posted"
    cancelled = "cancelled"


def test_normalize_decimal_to_string() -> None:
    out = _normalize_for_json({"amount": Decimal("1500.00")})
    assert out == {"amount": "1500.00"}


def test_normalize_uuid_to_string() -> None:
    u = uuid4()
    out = _normalize_for_json({"id": u})
    assert out == {"id": str(u)}


def test_normalize_datetime_to_iso() -> None:
    dt = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    out = _normalize_for_json({"at": dt})
    assert out == {"at": "2026-05-10T12:00:00+00:00"}


def test_normalize_enum_to_value() -> None:
    out = _normalize_for_json({"status": _Status.posted})
    assert out == {"status": "posted"}


def test_normalize_nested_structures() -> None:
    out = _normalize_for_json(
        {
            "entries": [
                {"amount": Decimal("100.00"), "type": _Status.posted},
                {"amount": Decimal("200.00"), "type": _Status.cancelled},
            ]
        }
    )
    assert out == {
        "entries": [
            {"amount": "100.00", "type": "posted"},
            {"amount": "200.00", "type": "cancelled"},
        ]
    }


def test_normalize_returns_none_for_none() -> None:
    assert _normalize_for_json(None) is None


# ---------------- Sensitive-key redaction ----------------


def test_normalize_redacts_password() -> None:
    out = _normalize_for_json({"password": "hunter2"})
    assert out == {"password": "***REDACTED***"}


def test_normalize_redacts_secret_and_token_and_api_key() -> None:
    out = _normalize_for_json(
        {
            "secret": "s",
            "token": "t",
            "api_key": "k",
            "API_KEY": "K",
            "auth_token": "x",
            "username": "alice",  # not redacted
        }
    )
    assert out == {
        "secret": "***REDACTED***",
        "token": "***REDACTED***",
        "api_key": "***REDACTED***",
        "API_KEY": "***REDACTED***",
        "auth_token": "***REDACTED***",
        "username": "alice",
    }


def test_normalize_redacts_nested_sensitive() -> None:
    out = _normalize_for_json(
        {
            "creds": {
                "user": "alice",
                "password": "hunter2",
                "extra": [{"token": "abc"}],
            }
        }
    )
    assert out == {
        "creds": {
            "user": "alice",
            "password": "***REDACTED***",
            "extra": [{"token": "***REDACTED***"}],
        }
    }


# ---------------- _compute_diff ----------------


def test_compute_diff_returns_changed_fields_only() -> None:
    diff = _compute_diff({"a": 1, "b": 2}, {"a": 1, "b": 3})
    assert diff == {"b": [2, 3]}


def test_compute_diff_handles_added_and_removed_keys() -> None:
    diff = _compute_diff({"a": 1, "b": 2}, {"a": 1, "c": 3})
    assert diff == {"b": [2, None], "c": [None, 3]}


def test_compute_diff_serializes_decimals() -> None:
    diff = _compute_diff(
        {"amount": Decimal("100.00")},
        {"amount": Decimal("150.00")},
    )
    assert diff == {"amount": ["100.00", "150.00"]}


def test_compute_diff_returns_empty_on_create_or_delete() -> None:
    assert _compute_diff(None, {"a": 1}) == {}
    assert _compute_diff({"a": 1}, None) == {}
    assert _compute_diff(None, None) == {}
