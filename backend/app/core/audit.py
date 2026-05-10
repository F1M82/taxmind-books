"""Audit emitter — single way to write audit_logs rows.

Per `docs/AUDIT.md`: every state-changing operation on a financially
significant entity produces exactly one audit row, written within the
same DB transaction as the action.

The emitter never commits. The caller's transaction commits the audit
row alongside the action it audits — atomicity guaranteed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.user import User

# ---------------------------------------------------------------------
# Allowed actions — add new entries to AUDIT.md *first*.
# ---------------------------------------------------------------------

_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "voucher.created",
        "voucher.updated",
        "voucher.cancelled",
        "voucher.posted_to_tally",
        "voucher.tally_post_failed",
        "voucher.posted_as_optional",
        "voucher.approved_to_regular",
        "voucher.rejected_optional",
        "ledger.created",
        "ledger.updated",
        "recon.session_created",
        "recon.session_completed",
        "recon.match_confirmed",
        "recon.match_rejected",
        "company.created",
        "company.settings_updated",
        "company.suspended",
        "user.created",
        "user.password_changed",
        "user.deactivated",
        "user_company.role_assigned",
        "user_company.role_changed",
        "user_company.removed",
        "narration_rule.created",
        "narration_rule.disabled",
        "account.deletion_requested",
        "account.deletion_cancelled",
        "account.deletion_completed",
        "data_export.requested",
        "data_export.completed",
        "device.registered",
        "device.unregistered",
    }
)

# Allowed `source` field values, matching the ck_audit_logs_source CHECK.
_ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"api", "worker", "connector", "system"}
)

# Sensitive key matchers. We redact regardless of nesting depth.
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|secret|token|api[_-]?key)"
)
_REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------
# Per-request context
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class AuditContext:
    """Per-request context passed to services for audit emission.

    Created in the API layer (from request) and passed to services.
    Workers create their own AuditContext from task arguments.
    """

    company: Company
    user: User | None
    ip_address: str | None
    user_agent: str | None
    request_id: UUID
    source: str

    def __post_init__(self) -> None:
        if self.source not in _ALLOWED_SOURCES:
            raise ValueError(
                f"source must be one of {sorted(_ALLOWED_SOURCES)}; "
                f"got {self.source!r}"
            )


# ---------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------


class AuditEmitter:
    """The single way to write audit_logs rows.

    Services receive an AuditEmitter via constructor injection and call
    `.emit()` with the action + before/after snapshots. The emitter
    appends the row to the current session — no commit here, the caller
    transaction commits it together with the audited action.
    """

    def __init__(self, db: Session, ctx: AuditContext) -> None:
        self.db = db
        self.ctx = ctx

    def emit(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID,
        old_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
    ) -> AuditLog:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"unknown audit action: {action!r}")
        log = AuditLog(
            id=uuid4(),
            company_id=self.ctx.company.id,
            user_id=self.ctx.user.id if self.ctx.user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=_normalize_for_json(old_value),
            new_value=_normalize_for_json(new_value),
            changes=_compute_diff(old_value, new_value),
            ip_address=self.ctx.ip_address,
            user_agent=self.ctx.user_agent,
            request_id=self.ctx.request_id,
            source=self.ctx.source,
        )
        self.db.add(log)
        return log


# ---------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------


def _normalize_for_json(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert Decimal / datetime / UUID / Enum to JSON-safe primitives.

    Recursively redacts any value whose key matches `password`,
    `secret`, `token`, or `api_key` (case-insensitive). Lists and
    nested dicts are walked.
    """
    if value is None:
        return None
    return _walk(value)  # type: ignore[return-value]


def _walk(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _SENSITIVE_KEY_RE.search(str(k)) else _walk(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    if isinstance(obj, tuple):
        return [_walk(v) for v in obj]
    if isinstance(obj, set | frozenset):
        return [_walk(v) for v in sorted(obj, key=str)]
    return obj


def _compute_diff(
    old: dict[str, Any] | None, new: dict[str, Any] | None
) -> dict[str, list[Any]]:
    """Field-level diff: { field: [old, new] } for changed keys only.

    Both sides null → empty diff. One side null → empty diff (treat as
    a CREATE or DELETE; the snapshot covers it). Returns serialized
    values so the result is JSON-safe.
    """
    if old is None or new is None:
        return {}
    keys = set(old) | set(new)
    return {
        k: [_walk(old.get(k)), _walk(new.get(k))]
        for k in sorted(keys)
        if old.get(k) != new.get(k)
    }
