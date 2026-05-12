"""AccountDeletionRequest — DPDP-compliant deletion lifecycle (P0.45).

Each row tracks one user's request to erase their account, including
the grace period before processing. The flow is:

    grace_period --(grace_ends_at passes)--> processing -> completed
                 --(user cancels)----------> cancelled
                                       (or)-> failed (failure_reason set)

The grace window (default 30 days, set explicitly by the service when
the row is inserted) is the cooling-off period DPDP expects. While
`grace_period`, the user may cancel by hitting DELETE on the
deletion-request endpoint.

Once processing starts, the worker hard-deletes the user record;
`account_deletion_requests.user_id` is `ON DELETE CASCADE`, so this
row goes with it. The durable trail of "the account was deleted" lives
in `audit_logs` (action=`account.deletion_completed`); that row's
`user_id` is `ON DELETE SET NULL`, so the audit row survives the
cascade with `user_id = NULL` — exactly the PII-scrub posture DPDP
requires.

`final_export_s3_key` is reserved for Phase 1's data-export-on-delete
integration. Phase 0 always leaves it NULL.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class AccountDeletionStatus(str, PyEnum):
    grace_period = "grace_period"
    cancelled = "cancelled"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"

    id: Mapped[UUID] = uuid_pk()

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    grace_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[AccountDeletionStatus] = mapped_column(
        SAEnum(
            AccountDeletionStatus,
            name="account_deletion_status",
            values_callable=lambda enum: [e.value for e in enum],
            native_enum=True,
        ),
        nullable=False,
        server_default=text("'grace_period'"),
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_export_s3_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        Index("idx_account_deletion_user", "user_id"),
        Index(
            "idx_account_deletion_grace_pending",
            "grace_ends_at",
            postgresql_where="status = 'grace_period'",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AccountDeletionRequest id={self.id} user={self.user_id} "
            f"status={self.status.value}>"
        )
