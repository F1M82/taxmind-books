"""AuditLog model — append-only ledger of every mutation.

Append-only is enforced at three layers (per `docs/AUDIT.md`): the API
service layer, this model, and a database trigger that raises on
UPDATE/DELETE. The trigger is defined in migration 0004.
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

from app.models.base import (
    Base,
    TenantScopedMixin,
    created_at_col,
    uuid_pk,
)


class AuditLog(Base, TenantScopedMixin):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = uuid_pk()

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
