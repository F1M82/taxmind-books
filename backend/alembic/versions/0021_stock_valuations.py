"""Per-financial-year stock valuation table.

TaxMind has no inventory model, so profit & loss omitted opening/closing
stock and the balance sheet showed stock at its opening value forever
(docs/PHASE_3_CLOSING_STOCK_DESIGN.md). This adds ``stock_valuations``: one
row per (company, financial year) holding Tally's opening and closing stock
VALUE, signed Dr-positive. Additive; no backfill, no effect until a row is
recorded.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_valuations",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("opening_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("closing_value", sa.Numeric(15, 2), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("negative_stock_items", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "captured_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "company_id", "period_from", name="uq_stock_valuations_company_fy"
        ),
        sa.CheckConstraint(
            "period_to > period_from", name="ck_stock_valuations_period_order"
        ),
        sa.CheckConstraint(
            "source IN ('tally', 'manual')", name="ck_stock_valuations_source"
        ),
    )
    op.create_index(
        "idx_stock_valuations_company", "stock_valuations", ["company_id"]
    )
    op.execute(
        "CREATE TRIGGER trg_stock_valuations_updated_at "
        "BEFORE UPDATE ON stock_valuations "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.drop_index("idx_stock_valuations_company", table_name="stock_valuations")
    op.drop_table("stock_valuations")
