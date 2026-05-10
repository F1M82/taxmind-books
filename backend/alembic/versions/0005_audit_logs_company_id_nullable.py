"""audit_logs.company_id NULLABLE (v1.2 Patch 1)

Drops the NOT NULL on `audit_logs.company_id` so system events
(`user.created`, `user.password_changed`, `user.deactivated`,
`device.registered/unregistered`, `account.deletion_*`,
`data_export.*`) — which have no tenant scope at the moment they
occur — can be audited as required by AUDIT.md.

Recreates `idx_audit_logs_company_created` as a partial index
(`WHERE company_id IS NOT NULL`) so tenant-scoped reads don't pay for
system-event rows.

See `docs/AMENDMENTS_v1.2.md` §"Patch 1" for the full rationale.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop NOT NULL.
    op.alter_column(
        "audit_logs",
        "company_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    # Recreate the company-scoped index as a partial index.
    op.drop_index(
        "idx_audit_logs_company_created", table_name="audit_logs"
    )
    op.create_index(
        "idx_audit_logs_company_created",
        "audit_logs",
        ["company_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Recreate the non-partial index first so the NOT NULL re-application
    # below has the prior shape to fall back to.
    op.drop_index(
        "idx_audit_logs_company_created", table_name="audit_logs"
    )
    op.create_index(
        "idx_audit_logs_company_created",
        "audit_logs",
        ["company_id", sa.text("created_at DESC")],
    )

    # Re-apply NOT NULL. Will fail if any system-event rows exist; that
    # is intentional — downgrade past Patch 1 means returning to a
    # schema that cannot represent system events, so they must be
    # cleared first.
    op.alter_column(
        "audit_logs",
        "company_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
