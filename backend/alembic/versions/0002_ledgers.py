"""ledgers table

Adds the ledger master table — chart-of-accounts row, mirror of a Tally
ledger. Tenant-scoped on `company_id`. Includes the `pg_trgm` extension
and the gin trigram index used by the fuzzy search in P0.17.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions + enum
    # ------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute("CREATE TYPE balance_type AS ENUM ('Dr', 'Cr')")

    # ------------------------------------------------------------------
    # ledgers
    # ------------------------------------------------------------------
    op.create_table(
        "ledgers",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_normalized", sa.String(255), nullable=False),
        sa.Column("group_name", sa.String(100), nullable=True),
        sa.Column(
            "parent_ledger_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledgers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "opening_balance",
            sa.Numeric(15, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "balance_type",
            postgresql.ENUM(
                "Dr",
                "Cr",
                name="balance_type",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'Dr'"),
        ),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=True),
        sa.Column("phone", sa.String(15), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column("tally_master_id", sa.String(100), nullable=True),
        sa.Column(
            "tally_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
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
            "company_id", "name", name="uq_ledgers_company_name"
        ),
        sa.UniqueConstraint(
            "company_id", "tally_master_id", name="uq_ledgers_company_tally"
        ),
        sa.CheckConstraint(
            r"gstin IS NULL OR gstin ~ "
            r"'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="ck_ledgers_gstin_format",
        ),
        sa.CheckConstraint(
            r"pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'",
            name="ck_ledgers_pan_format",
        ),
    )

    op.create_index("idx_ledgers_company", "ledgers", ["company_id"])
    op.create_index(
        "idx_ledgers_company_active", "ledgers", ["company_id", "is_active"]
    )
    op.create_index(
        "idx_ledgers_company_group", "ledgers", ["company_id", "group_name"]
    )
    op.create_index(
        "idx_ledgers_gstin",
        "ledgers",
        ["company_id", "gstin"],
        postgresql_where=sa.text("gstin IS NOT NULL"),
    )
    op.create_index(
        "idx_ledgers_pan",
        "ledgers",
        ["company_id", "pan"],
        postgresql_where=sa.text("pan IS NOT NULL"),
    )
    # gin trigram index — fuzzy search in P0.17.
    op.execute(
        "CREATE INDEX idx_ledgers_name_trgm ON ledgers "
        "USING gin (name_normalized gin_trgm_ops)"
    )

    op.execute(
        "CREATE TRIGGER trg_ledgers_updated_at BEFORE UPDATE ON ledgers "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ledgers_updated_at ON ledgers")
    op.execute("DROP INDEX IF EXISTS idx_ledgers_name_trgm")
    op.drop_index("idx_ledgers_pan", table_name="ledgers")
    op.drop_index("idx_ledgers_gstin", table_name="ledgers")
    op.drop_index("idx_ledgers_company_group", table_name="ledgers")
    op.drop_index("idx_ledgers_company_active", table_name="ledgers")
    op.drop_index("idx_ledgers_company", table_name="ledgers")
    op.drop_table("ledgers")

    op.execute("DROP TYPE IF EXISTS balance_type")
    # pg_trgm extension is left in place — other migrations may rely on it.
