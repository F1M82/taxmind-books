"""voucher client_channel — which client posted the entry

Adds `vouchers.client_channel`, orthogonal to `source`. `source` records
*how the entry was captured* (manual / photo / pdf / voice / …);
`client_channel` records *which client posted it* (mobile / web / api).
A phone user can record a `manual` entry OR upload a `photo` — both are
`client_channel='mobile'` — so the two dimensions must not be conflated.

Nullable with no default: existing rows and any writer that does not send
the `X-Client` header stay NULL ("unknown / legacy"). A CHECK constraint
pins the allowed non-null values so a typo can't silently land.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("client_channel", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_vouchers_client_channel",
        "vouchers",
        "client_channel IN ('mobile', 'web', 'api') "
        "OR client_channel IS NULL",
    )
    # Partial index so "entries recorded from mobile" filters cheaply.
    op.create_index(
        "idx_vouchers_client_channel",
        "vouchers",
        ["company_id", "client_channel"],
        postgresql_where=sa.text("client_channel IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_vouchers_client_channel", table_name="vouchers")
    op.drop_constraint(
        "ck_vouchers_client_channel", "vouchers", type_="check"
    )
    op.drop_column("vouchers", "client_channel")
