"""Unit tests for AuditLog and IdempotencyKey models."""

from __future__ import annotations

from app.models.audit_log import AuditLog
from app.models.base import TenantScopedMixin
from app.models.idempotency_key import IdempotencyKey
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB

# ---------------- AuditLog ----------------


def test_audit_log_tablename() -> None:
    assert AuditLog.__tablename__ == "audit_logs"


def test_audit_log_not_tenant_scoped_mixin() -> None:
    """AuditLog is intentionally NOT a TenantScopedMixin subclass.

    The mixin enforces NOT NULL on company_id; per AMENDMENTS_v1.2
    Patch 1, system events (user.created, user.password_changed,
    user.deactivated, etc.) carry company_id = NULL. Tenant scoping
    for the audit-log read API is applied explicitly in service code.
    """
    assert not issubclass(AuditLog, TenantScopedMixin)


def test_audit_log_company_id_is_nullable() -> None:
    col = AuditLog.__table__.columns["company_id"]
    assert col.nullable is True


def test_audit_log_columns_match_schema() -> None:
    cols = {c.name for c in AuditLog.__table__.columns}
    assert cols == {
        "id",
        "company_id",
        "user_id",
        "action",
        "entity_type",
        "entity_id",
        "old_value",
        "new_value",
        "changes",
        "ip_address",
        "user_agent",
        "request_id",
        "source",
        "created_at",
    }


def test_audit_log_company_id_restrict_on_company_delete() -> None:
    fk = next(iter(AuditLog.__table__.columns["company_id"].foreign_keys))
    assert fk.ondelete == "RESTRICT"


def test_audit_log_user_id_set_null_on_user_delete() -> None:
    fk = next(iter(AuditLog.__table__.columns["user_id"].foreign_keys))
    assert fk.ondelete == "SET NULL"


def test_audit_log_action_limit_40() -> None:
    col = AuditLog.__table__.columns["action"]
    assert isinstance(col.type, String)
    assert col.type.length == 40
    assert col.nullable is False


def test_audit_log_jsonb_columns() -> None:
    for name in ("old_value", "new_value", "changes"):
        assert isinstance(AuditLog.__table__.columns[name].type, JSONB)


def test_audit_log_changes_default_empty_jsonb() -> None:
    col = AuditLog.__table__.columns["changes"]
    assert col.nullable is False
    assert "{}" in str(col.server_default.arg)


def test_audit_log_ip_address_inet() -> None:
    col = AuditLog.__table__.columns["ip_address"]
    assert isinstance(col.type, INET)
    assert col.nullable is True


def test_audit_log_user_agent_text() -> None:
    assert isinstance(AuditLog.__table__.columns["user_agent"].type, Text)


def test_audit_log_source_check_constraint_present() -> None:
    names = {c.name for c in AuditLog.__table__.constraints if c.name is not None}
    assert "ck_audit_logs_source" in names


def test_audit_log_indexes() -> None:
    names = {ix.name for ix in AuditLog.__table__.indexes}
    assert {
        "idx_audit_logs_company_created",
        "idx_audit_logs_entity",
        "idx_audit_logs_user",
        "idx_audit_logs_request",
    }.issubset(names)


def test_audit_log_created_at_has_tz() -> None:
    col = AuditLog.__table__.columns["created_at"]
    assert isinstance(col.type, DateTime)
    assert col.type.timezone is True


def test_audit_log_no_updated_at() -> None:
    """audit_logs is append-only — there is no updated_at column."""
    cols = {c.name for c in AuditLog.__table__.columns}
    assert "updated_at" not in cols


# ---------------- IdempotencyKey ----------------


def test_idempotency_key_tablename() -> None:
    assert IdempotencyKey.__tablename__ == "idempotency_keys"


def test_idempotency_key_columns_match_schema() -> None:
    cols = {c.name for c in IdempotencyKey.__table__.columns}
    assert cols == {
        "id",
        "company_id",
        "user_id",
        "key",
        "method",
        "path",
        "request_hash",
        "response_status",
        "response_body",
        "response_headers",
        "locked_at",
        "completed_at",
        "created_at",
        "expires_at",
    }


def test_idempotency_key_company_cascade_on_company_delete() -> None:
    fk = next(iter(IdempotencyKey.__table__.columns["company_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_idempotency_key_user_cascade_on_user_delete() -> None:
    fk = next(iter(IdempotencyKey.__table__.columns["user_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_idempotency_key_key_required_255() -> None:
    col = IdempotencyKey.__table__.columns["key"]
    assert isinstance(col.type, String)
    assert col.type.length == 255
    assert col.nullable is False


def test_idempotency_key_method_required_10() -> None:
    col = IdempotencyKey.__table__.columns["method"]
    assert isinstance(col.type, String)
    assert col.type.length == 10
    assert col.nullable is False


def test_idempotency_key_path_500() -> None:
    col = IdempotencyKey.__table__.columns["path"]
    assert isinstance(col.type, String)
    assert col.type.length == 500


def test_idempotency_key_request_hash_64() -> None:
    col = IdempotencyKey.__table__.columns["request_hash"]
    assert isinstance(col.type, String)
    assert col.type.length == 64


def test_idempotency_key_response_status_int() -> None:
    col = IdempotencyKey.__table__.columns["response_status"]
    assert isinstance(col.type, Integer)
    assert col.nullable is True


def test_idempotency_key_jsonb_response_columns() -> None:
    for name in ("response_body", "response_headers"):
        assert isinstance(IdempotencyKey.__table__.columns[name].type, JSONB)


def test_idempotency_key_unique_company_key() -> None:
    names = {
        c.name for c in IdempotencyKey.__table__.constraints if c.name is not None
    }
    assert "uq_idempotency_keys_company_key" in names


def test_idempotency_key_indexes() -> None:
    names = {ix.name for ix in IdempotencyKey.__table__.indexes}
    assert "idx_idempotency_keys_expires" in names


def test_idempotency_key_timestamps_have_tz() -> None:
    for name in ("locked_at", "completed_at", "created_at", "expires_at"):
        col = IdempotencyKey.__table__.columns[name]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True


def test_idempotency_key_expires_at_required() -> None:
    assert IdempotencyKey.__table__.columns["expires_at"].nullable is False
