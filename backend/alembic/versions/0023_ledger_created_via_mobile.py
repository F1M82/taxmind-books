"""ledger `created_via_mobile` + `confirmed_in_tally_at` (v1.3 item 7)

Implements the remaining v1.3 amendment item: ledgers created from the
mobile app are pushed to Tally by the connector (new `create_ledger`
command) rather than staying DB-only. Until Tally confirms the create,
any voucher referencing such a ledger is forced Optional regardless of
confidence (`created_via_mobile=true AND confirmed_in_tally_at IS NULL`
per AMENDMENTS_v1.3.md). A ledger inserted any other way (sync_masters,
an internal script) defaults `created_via_mobile=false` and is
unaffected -- the existing BUG-005 unsynced-ledger hard block still
covers it exactly as before.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ledgers",
        sa.Column(
            "created_via_mobile",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.add_column(
        "ledgers",
        sa.Column(
            "confirmed_in_tally_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Partial index for the voucher-create-time Optional-tainting lookup:
    # "does this ledger still need tainting" is exactly
    # created_via_mobile AND confirmed_in_tally_at IS NULL.
    op.create_index(
        "idx_ledgers_pending_mobile_confirmation",
        "ledgers",
        ["company_id"],
        postgresql_where=sa.text(
            "created_via_mobile = TRUE AND confirmed_in_tally_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_ledgers_pending_mobile_confirmation", table_name="ledgers"
    )
    op.drop_column("ledgers", "confirmed_in_tally_at")
    op.drop_column("ledgers", "created_via_mobile")
