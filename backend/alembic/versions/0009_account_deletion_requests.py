"""account_deletion_requests table (DPDP — P0.45)

DPDP-compliant deletion lifecycle: grace_period -> processing
-> completed (or cancelled). See docs/SCHEMA.sql §"account_
deletion_requests" and docs/API.md §"Account & Data Lifecycle"
for the contract.

`user_id` is `ON DELETE CASCADE` so the row disappears with the
hard-deleted user; the durable trail of "the account was deleted"
lives in `audit_logs` (action=`account.deletion_completed`), whose
`user_id` is `ON DELETE SET NULL`.

`account_deletion_status` enum is local to this migration.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE account_deletion_status AS ENUM ("
        "'grace_period', 'cancelled', 'processing', "
        "'completed', 'failed')"
    )

    op.create_table(
        "account_deletion_requests",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "grace_ends_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "grace_period",
                "cancelled",
                "processing",
                "completed",
                "failed",
                name="account_deletion_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'grace_period'"),
        ),
        sa.Column(
            "cancelled_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("final_export_s3_key", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_index(
        "idx_account_deletion_user",
        "account_deletion_requests",
        ["user_id"],
    )
    op.create_index(
        "idx_account_deletion_grace_pending",
        "account_deletion_requests",
        ["grace_ends_at"],
        postgresql_where=sa.text("status = 'grace_period'"),
    )

    op.execute(
        "CREATE TRIGGER trg_account_deletion_updated_at "
        "BEFORE UPDATE ON account_deletion_requests "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_account_deletion_updated_at "
        "ON account_deletion_requests"
    )
    op.drop_index(
        "idx_account_deletion_grace_pending",
        table_name="account_deletion_requests",
    )
    op.drop_index(
        "idx_account_deletion_user",
        table_name="account_deletion_requests",
    )
    op.drop_table("account_deletion_requests")
    op.execute("DROP TYPE account_deletion_status")
