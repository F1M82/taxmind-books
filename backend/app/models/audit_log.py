"""AuditLog model — append-only ledger of every mutation.

Append-only is enforced at three layers (per `docs/AUDIT.md`): the API
service layer, this model, and a database trigger that raises on
UPDATE/DELETE. The trigger is defined in migration 0004.

`AuditLog` does **not** inherit `TenantScopedMixin`. Most rows are
tenant-scoped via their non-null `company_id`, but system events
(`user.created`, `user.password_changed`, `user.deactivated`, device
and account-lifecycle events) carry `company_id = NULL`. The mixin
would enforce NOT NULL and silently auto-filter them out of every
query — both wrong for this table.

Tenant-scoped reads of the audit log filter on `company_id` explicitly
in the read API (P0.20+); system rows are reachable only via
admin/superuser paths planned for Phase 5+. See AUDIT.md §"Tenant-
scoped vs system events" and AMENDMENTS_v1.2.md §"Patch 1".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = uuid_pk()

    # Nullable: system events (see module docstring).
    company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
    )

    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )

    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    changes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::JSONB"),
    )

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        CheckConstraint(
            "source IN ('api', 'worker', 'connector', 'system')",
            name="ck_audit_logs_source",
        ),
        Index(
            "idx_audit_logs_company_created",
            "company_id",
            text("created_at DESC"),
            postgresql_where="company_id IS NOT NULL",
        ),
        Index(
            "idx_audit_logs_entity",
            "entity_type",
            "entity_id",
        ),
        Index(
            "idx_audit_logs_user",
            "user_id",
            text("created_at DESC"),
            postgresql_where="user_id IS NOT NULL",
        ),
        Index(
            "idx_audit_logs_request",
            "request_id",
            postgresql_where="request_id IS NOT NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"entity={self.entity_type}/{self.entity_id}>"
        )
