"""voucher status `pending_tally_post` + tally_post_queued_at (P0.46d)

Splits the v1.2 conflation of `status='posted'` with "the voucher has
reached Tally". After this migration:

  - `pending_tally_post` is the initial state for any voucher that has
    been written to our books but not yet confirmed by Tally.
  - `posted` is only set by the dispatcher once Tally returns success.
  - `tally_post_queued_at` records when the wait started, so the v1.3
    expiry sweep (P0.54) has a starting clock without scanning audit
    rows.

The partial index `idx_vouchers_unposted_to_tally` is repointed at
`status = 'pending_tally_post'`; the old predicate would be empty by
construction once the lifecycle change lands.

The v1.3 `tally_post_expired` enum value is intentionally NOT added in
this migration — see PHASE_0_TASKS.md P0.54 for the Phase 0.5 task that
introduces both the value and the daily expiry beat job.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    # PostgreSQL; mirror the autocommit_block pattern used by 0007.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE voucher_status "
            "ADD VALUE IF NOT EXISTS 'pending_tally_post'"
        )

    op.add_column(
        "vouchers",
        sa.Column(
            "tally_post_queued_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Repoint the retry-target index. The old predicate
    # `status = 'posted' AND tally_posted_at IS NULL` was the v1.2 hint
    # for "needs a Tally post"; that combination is unreachable under
    # the new lifecycle (dispatcher stamps tally_posted_at atomically
    # with the pending → posted transition).
    op.drop_index("idx_vouchers_unposted_to_tally", table_name="vouchers")
    op.create_index(
        "idx_vouchers_unposted_to_tally",
        "vouchers",
        ["company_id"],
        postgresql_where=sa.text("status = 'pending_tally_post'"),
    )


def downgrade() -> None:
    # Both partial indexes hold parsed references to the current
    # voucher_status type via their WHERE clauses; the ALTER TYPE
    # rebuild below would leave them pointing at voucher_status_old
    # and the USING cast would fail with `operator does not exist:
    # voucher_status <> voucher_status_old`. Drop both here and
    # recreate after the rebuild — same pattern as 0007's downgrade.
    op.drop_index("idx_vouchers_optional_pending", table_name="vouchers")
    op.drop_index("idx_vouchers_unposted_to_tally", table_name="vouchers")
    op.drop_column("vouchers", "tally_post_queued_at")

    # Postgres has no DROP VALUE for enums; rebuild the type without
    # `pending_tally_post`. Any rows still in that state would block
    # the cast — by contract, downgrade is only safe once all such
    # rows have transitioned to `posted` (or been cancelled).
    op.execute("ALTER TYPE voucher_status RENAME TO voucher_status_old")
    op.execute(
        "CREATE TYPE voucher_status AS ENUM "
        "('draft', 'pending_approval', 'optional', 'posted', "
        "'cancelled', 'rejected_optional')"
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
        postgresql_where=sa.text(
            "status = 'posted' AND tally_posted_at IS NULL"
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
