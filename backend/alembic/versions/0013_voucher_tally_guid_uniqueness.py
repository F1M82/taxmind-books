"""voucher identity: Tally GUID becomes durable key, number is display (P3.7, Migration B)

Real Tally evidence (2026-08-14 probe): two distinct, live Sales vouchers
share `VAC/25-26/222` across financial years, with different native GUIDs
(MASTERID 7896 vs 8090). Therefore

    (company_id, voucher_type, voucher_number)

is NOT a valid uniqueness key. The approved identity rule is

    (company_id, tally_guid)  — valid only when tally_guid IS NOT NULL

So this migration:

  1. creates the partial unique index
        uq_vouchers_company_tally_guid
        UNIQUE (company_id, tally_guid) WHERE tally_guid IS NOT NULL
     FIRST — preserving uniqueness for any already-populated tally_guid
     while the old key is still in force; and
  2. drops the old constraint uq_vouchers_company_number_type.

The old constraint was DEFERRABLE INITIALLY DEFERRED so a write-then-
renumber insert could rewrite voucher_number mid-transaction. The new
index is intentionally NOT deferrable: `tally_guid` is written once and
never rewritten, and immediate uniqueness guards the import upsert
(`same tally_guid → update`, `different tally_guid → new row`).

`voucher_number` remains an informational/display column; duplicates are
permitted under the new identity model (GATE 4).

Do NOT repurpose `tally_voucher_guid` (the TaxMind REMOTEID / dispatch
identity); it is untouched by this migration.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) New partial unique index FIRST: existing rows all have
    #    tally_guid IS NULL, so the index builds instantly and preserves
    #    the old uniqueness guarantee until the constraint is dropped.
    op.create_index(
        "uq_vouchers_company_tally_guid",
        "vouchers",
        ["company_id", "tally_guid"],
        unique=True,
        postgresql_where=sa.text("tally_guid IS NOT NULL"),
    )

    # 2) Old (non-durable) voucher-number key is no longer an identity.
    op.drop_constraint(
        "uq_vouchers_company_number_type", "vouchers", type_="unique"
    )


def downgrade() -> None:
    # Recreate the old constraint EXACTLY as it existed: deferrable,
    # so a write-then-renumber insert can rewrite voucher_number within
    # one transaction. op.create_unique_constraint() does not expose
    # deferrability, hence raw SQL.
    op.execute(
        "ALTER TABLE vouchers ADD CONSTRAINT uq_vouchers_company_number_type "
        "UNIQUE (company_id, voucher_type, voucher_number) "
        "DEFERRABLE INITIALLY DEFERRED"
    )

    op.drop_index("uq_vouchers_company_tally_guid", table_name="vouchers")
