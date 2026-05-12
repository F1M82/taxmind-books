"""voucher optional fields (v1.2)

Adds the v1.2 Optional voucher flow columns to `vouchers` and the
`'optional'` / `'rejected_optional'` values to the `voucher_status`
enum. Also adds the partial index that surfaces Optional vouchers
awaiting approval. See PHASE_0_TASKS.md P0.37 and SCHEMA.sql §vouchers.

Existing rows default to `is_optional_in_tally = FALSE`, so the
historical manual-only behaviour is preserved.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    # PostgreSQL; using autocommit_block keeps this portable.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE voucher_status ADD VALUE IF NOT EXISTS 'optional'")
        op.execute(
            "ALTER TYPE voucher_status "
            "ADD VALUE IF NOT EXISTS 'rejected_optional'"
        )

    op.add_column(
        "vouchers",
        sa.Column(
            "is_optional_in_tally",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "approved_to_regular_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "approved_to_regular_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column("optional_rejection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "optional_rejected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "optional_rejected_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_vouchers_optional_pending",
        "vouchers",
        ["company_id", sa.text("date DESC")],
        postgresql_where=sa.text(
            "is_optional_in_tally = TRUE "
            "AND approved_to_regular_at IS NULL "
            "AND status NOT IN ('cancelled', 'rejected_optional')"
        ),
    )


def downgrade() -> None:
    op.drop_index("idx_vouchers_optional_pending", table_name="vouchers")

    op.drop_column("vouchers", "optional_rejected_by")
    op.drop_column("vouchers", "optional_rejected_at")
    op.drop_column("vouchers", "optional_rejection_reason")
    op.drop_column("vouchers", "approved_to_regular_by")
    op.drop_column("vouchers", "approved_to_regular_at")
    op.drop_column("vouchers", "is_optional_in_tally")

    # Postgres has no DROP VALUE for enums; rebuild the type without the
    # v1.2 values. Any rows still using them would block the cast — but
    # by contract no such rows exist when the v1.2 columns are gone.
    op.execute("ALTER TYPE voucher_status RENAME TO voucher_status_old")
    op.execute(
        "CREATE TYPE voucher_status AS ENUM "
        "('draft', 'pending_approval', 'posted', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE vouchers ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE voucher_status "
        "USING status::text::voucher_status, "
        "ALTER COLUMN status SET DEFAULT 'posted'"
    )
    op.execute("DROP TYPE voucher_status_old")
