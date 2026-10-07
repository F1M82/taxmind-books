"""voucher status `tally_post_expired` (v1.3 P0.54, 30-day expiry)

Implements the expiry half of the v1.3 queue-on-mismatch design that
migration 0010 explicitly deferred: a voucher stuck in
`pending_tally_post` for more than REENQUEUE_WINDOW (30 days) — with a
latest tally-post audit action of `voucher.tally_post_queued` — is
marked `tally_post_expired` by the expiry sweep, audited, and surfaced
to the operator for manual handling. Expired vouchers are NOT live book
entries (they never reached Tally): every report filter is an explicit
allow-list (`status.in_([posted, pending_tally_post])`), so the new
value is excluded from trial balance / P&L / dashboard / onboarding by
construction, like `rejected_optional`.

Pure enum-value addition — no column, index, or backfill changes.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    # PostgreSQL; mirror the autocommit_block pattern used by 0007/0010.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voucher_status "
            "ADD VALUE IF NOT EXISTS 'tally_post_expired'"
        )


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; rebuild the type without
    # `tally_post_expired`. The two partial indexes on vouchers carry
    # parsed references to the current voucher_status type via their
    # WHERE clauses; the rebuild would leave them pointing at
    # voucher_status_old and the USING cast would fail — so drop both
    # here and recreate after the rebuild (same pattern as 0010's
    # downgrade). By contract, downgrade is only safe once no rows are
    # still `tally_post_expired` (they'd block the cast).
    op.drop_index("idx_vouchers_optional_pending", table_name="vouchers")
    op.drop_index("idx_vouchers_unposted_to_tally", table_name="vouchers")

    op.execute("ALTER TYPE voucher_status RENAME TO voucher_status_old")
    op.execute(
        "CREATE TYPE voucher_status AS ENUM "
        "('draft', 'pending_approval', 'optional', 'pending_tally_post', "
        "'posted', 'cancelled', 'rejected_optional')"
    )
    op.execute(
        "ALTER TABLE vouchers ALTER COLUMN status DROP DEFAULT, "
        "ALTER COLUMN status TYPE voucher_status "
        "USING status::text::voucher_status, "
        "ALTER COLUMN status SET DEFAULT 'posted'"
    )
    op.execute("DROP TYPE voucher_status_old")

    op.create_index(
        "idx_vouchers_unposted_to_tally",
        "vouchers",
        ["company_id"],
        postgresql_where=sa.text("status = 'pending_tally_post'"),
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
