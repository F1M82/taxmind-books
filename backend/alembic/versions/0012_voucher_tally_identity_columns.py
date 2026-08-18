"""voucher Tally identity columns (P3.7, Migration A)

Adds the four nullable Tally-identity columns to `vouchers`:

  - `tally_guid`       — Tally's native voucher GUID (the *durable*
    identity for imported Tally vouchers, once backfilled).
  - `tally_master_id`  — Tally's MASTERID (change/version metadata only,
    NOT durable identity; mirrors the misnomer already in `ledgers`).
  - `tally_vchkey`     — Tally's VCHKEY (change/version metadata only).
  - `tally_alter_id`   — Tally's ALTERID (change/version metadata only).

Do NOT confuse these with the existing `tally_voucher_guid` column,
which is the TaxMind-issued REMOTEID / dispatch identity (BUG-004
Layer C). `tally_voucher_guid` semantics are unchanged by this task.

All columns are nullable and unbackfilled: existing rows keep NULL until
a future founder-approved import/backfill populates them.

Migration B (0013) introduces `UNIQUE (company_id, tally_guid) WHERE
tally_guid IS NOT NULL` and removes `uq_vouchers_company_number_type`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("tally_guid", sa.String(100), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_master_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_vchkey", sa.String(150), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_alter_id", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vouchers", "tally_alter_id")
    op.drop_column("vouchers", "tally_vchkey")
    op.drop_column("vouchers", "tally_master_id")
    op.drop_column("vouchers", "tally_guid")
