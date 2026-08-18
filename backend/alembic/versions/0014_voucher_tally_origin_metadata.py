"""voucher import-readiness metadata (P3.7, Phase 6A)

Makes the TaxMind voucher classification tolerant of Tally types the
TaxMind accounting model does not recognise, and records Tally's source
state verbatim without corrupting TaxMind semantics.

Changes (all additive except the column relaxation):

  1. `vouchers.voucher_type` becomes NULLABLE. NULL means "this voucher
     carries a Tally VOUCHERTYPENAME that does not map to a TaxMind
     VoucherType". The raw name is preserved in `tally_voucher_type`.
     TaxMind-created vouchers are unchanged: the API still requires a
     known VoucherType (schema-enforced, not DB-enforced).

  2. `tally_voucher_type VARCHAR(100) NULL` — the exact raw Tally
     VOUCHERTYPENAME, always preserved verbatim. Known types set BOTH
     the enum and this raw column; unknown types set the enum NULL and
     only this raw column.

  3. `tally_is_cancelled / tally_is_deleted / tally_is_optional`
     BOOLEAN NULL — Tally source-of-truth state. NULL means the source
     did not supply it. These NEVER mutate TaxMind `status`; in
     particular ISDELETED=Yes MUST NOT become status='cancelled'.

No defaults. No backfill. No new uniqueness constraints.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VOUCHER_TYPE_ENUM = postgresql.ENUM(
    "Receipt",
    "Payment",
    "Sales",
    "Purchase",
    "Journal",
    "Contra",
    "Debit Note",
    "Credit Note",
    name="voucher_type",
    create_type=False,
)


def upgrade() -> None:
    op.alter_column(
        "vouchers",
        "voucher_type",
        existing_type=_VOUCHER_TYPE_ENUM,
        nullable=True,
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_voucher_type", sa.String(100), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_is_cancelled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_is_deleted", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("tally_is_optional", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vouchers", "tally_is_optional")
    op.drop_column("vouchers", "tally_is_deleted")
    op.drop_column("vouchers", "tally_is_cancelled")
    op.drop_column("vouchers", "tally_voucher_type")
    op.alter_column(
        "vouchers",
        "voucher_type",
        existing_type=_VOUCHER_TYPE_ENUM,
        nullable=False,
    )
