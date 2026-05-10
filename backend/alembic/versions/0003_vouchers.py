"""vouchers + ledger_entries tables

Adds the financial heart of the system. Tenant-scoped on `company_id`.
The `voucher_number` uniqueness constraint is DEFERRABLE INITIALLY
DEFERRED so a single transaction can rewrite the number after row
creation. Ledger entries cascade on voucher delete.

The v1.2 Optional-voucher columns and the `'optional'` /
`'rejected_optional'` status values land in P0.37 / migration 0010.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enums
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TYPE voucher_type AS ENUM "
        "('Receipt', 'Payment', 'Sales', 'Purchase', "
        "'Journal', 'Contra', 'Debit Note', 'Credit Note')"
    )
    op.execute(
        "CREATE TYPE voucher_status AS ENUM "
        "('draft', 'pending_approval', 'posted', 'cancelled')"
    )
    op.execute("CREATE TYPE entry_type AS ENUM ('Dr', 'Cr')")

    # ------------------------------------------------------------------
    # vouchers
    # ------------------------------------------------------------------
    op.create_table(
        "vouchers",
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
        sa.Column(
            "voucher_type",
            postgresql.ENUM(
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
            ),
            nullable=False,
        ),
        sa.Column("voucher_number", sa.String(50), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft",
                "pending_approval",
                "posted",
                "cancelled",
                name="voucher_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'posted'"),
        ),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "source_ingestion_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "is_auto_posted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        # GST
        sa.Column(
            "gst_applicable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("place_of_supply", sa.String(2), nullable=True),
        sa.Column(
            "cgst",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sgst",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "igst",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cess",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # TDS
        sa.Column(
            "tds_applicable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "tds_amount",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("tds_section", sa.String(10), nullable=True),
        # Tally posting
        sa.Column(
            "tally_posted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("tally_voucher_guid", sa.String(100), nullable=True),
        sa.Column(
            "tally_post_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("tally_last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "company_id",
            "voucher_type",
            "voucher_number",
            name="uq_vouchers_company_number_type",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "total_amount >= 0", name="ck_vouchers_total_positive"
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_vouchers_confidence_range",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'whatsapp', 'sms', 'email', 'photo', 'pdf', "
            "'csv', 'voice', 'tally_sync', 'recon')",
            name="ck_vouchers_source",
        ),
        sa.CheckConstraint(
            "cgst >= 0 AND sgst >= 0 AND igst >= 0 AND cess >= 0",
            name="ck_vouchers_gst_components",
        ),
        sa.CheckConstraint(
            "tds_amount >= 0 "
            "AND (NOT tds_applicable OR tds_section IS NOT NULL)",
            name="ck_vouchers_tds",
        ),
        sa.CheckConstraint(
            r"place_of_supply IS NULL OR place_of_supply ~ '^[0-9]{2}$'",
            name="ck_vouchers_place_of_supply",
        ),
    )

    op.create_index(
        "idx_vouchers_company_date",
        "vouchers",
        ["company_id", sa.text("date DESC")],
    )
    op.create_index(
        "idx_vouchers_company_status",
        "vouchers",
        ["company_id", "status"],
    )
    op.create_index(
        "idx_vouchers_company_type_date",
        "vouchers",
        ["company_id", "voucher_type", sa.text("date DESC")],
    )
    op.create_index(
        "idx_vouchers_source_ingestion",
        "vouchers",
        ["source_ingestion_id"],
        postgresql_where=sa.text("source_ingestion_id IS NOT NULL"),
    )
    op.create_index(
        "idx_vouchers_unposted_to_tally",
        "vouchers",
        ["company_id"],
        postgresql_where=sa.text(
            "status = 'posted' AND tally_posted_at IS NULL"
        ),
    )

    op.execute(
        "CREATE TRIGGER trg_vouchers_updated_at BEFORE UPDATE ON vouchers "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ------------------------------------------------------------------
    # ledger_entries
    # ------------------------------------------------------------------
    op.create_table(
        "ledger_entries",
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
        sa.Column(
            "voucher_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vouchers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ledger_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledgers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column(
            "entry_type",
            postgresql.ENUM(
                "Dr",
                "Cr",
                name="entry_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("gst_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("cgst", sa.Numeric(15, 2), nullable=True),
        sa.Column("sgst", sa.Numeric(15, 2), nullable=True),
        sa.Column("igst", sa.Numeric(15, 2), nullable=True),
        sa.Column("tds_amount", sa.Numeric(15, 2), nullable=True),
        sa.Column("tds_section", sa.String(10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "voucher_id", "line_number", name="uq_ledger_entries_voucher_line"
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_ledger_entries_amount_positive"
        ),
        sa.CheckConstraint(
            "gst_rate IS NULL OR (gst_rate >= 0 AND gst_rate <= 100)",
            name="ck_ledger_entries_gst_rate",
        ),
    )

    op.create_index(
        "idx_ledger_entries_voucher", "ledger_entries", ["voucher_id"]
    )
    op.create_index(
        "idx_ledger_entries_ledger", "ledger_entries", ["ledger_id"]
    )
    op.create_index(
        "idx_ledger_entries_company_ledger",
        "ledger_entries",
        ["company_id", "ledger_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ledger_entries_company_ledger", table_name="ledger_entries")
    op.drop_index("idx_ledger_entries_ledger", table_name="ledger_entries")
    op.drop_index("idx_ledger_entries_voucher", table_name="ledger_entries")
    op.drop_table("ledger_entries")

    op.execute("DROP TRIGGER IF EXISTS trg_vouchers_updated_at ON vouchers")
    op.drop_index("idx_vouchers_unposted_to_tally", table_name="vouchers")
    op.drop_index("idx_vouchers_source_ingestion", table_name="vouchers")
    op.drop_index("idx_vouchers_company_type_date", table_name="vouchers")
    op.drop_index("idx_vouchers_company_status", table_name="vouchers")
    op.drop_index("idx_vouchers_company_date", table_name="vouchers")
    op.drop_table("vouchers")

    op.execute("DROP TYPE IF EXISTS entry_type")
    op.execute("DROP TYPE IF EXISTS voucher_status")
    op.execute("DROP TYPE IF EXISTS voucher_type")
