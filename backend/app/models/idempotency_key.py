"""IdempotencyKey model — request-deduplication record.

Per `docs/IDEMPOTENCY.md`: a row is created on first request, locked
during processing, and stamped with the response on completion. A
second request with the same key + body returns the stored response.

`company_id` here uses CASCADE rather than the standard RESTRICT — when
a company is hard-deleted (DPDP grace expiry), its idempotency state
goes with it. So this model declares its own `company_id` instead of
using `TenantScopedMixin`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[UUID] = uuid_pk()

    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    key: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    response_headers: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id", "key", name="uq_idempotency_keys_company_key"
        ),
        Index("idx_idempotency_keys_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<IdempotencyKey id={self.id} key={self.key!r}>"
