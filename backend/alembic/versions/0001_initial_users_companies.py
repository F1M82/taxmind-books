"""initial — users, companies, user_companies

Creates the foundational identity tables plus the cross-cutting
`set_updated_at()` trigger function used by every table that has an
`updated_at` column. The pgcrypto extension is enabled for
`gen_random_uuid()`.

Revision ID: 0001
Revises:
Create Date: 2026-05-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions + shared trigger function
    # ------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # ------------------------------------------------------------------
    # Enums
    # ------------------------------------------------------------------
    # Raw DDL (rather than sa.Enum().create()) so the offline
    # `alembic upgrade head --sql` output matches what runs online —
    # alembic's offline mode does not honor `create_type=False` on the
    # column-level Enum and would otherwise emit duplicate CREATE TYPE.
    op.execute(
        "CREATE TYPE company_status AS ENUM ('active', 'inactive', 'suspended')"
    )
    op.execute(
        "CREATE TYPE company_role AS ENUM "
        "('owner', 'admin', 'accountant', 'viewer')"
    )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(15), nullable=True),
        sa.Column(
            "is_ca",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("firm_name", sa.String(255), nullable=True),
        sa.Column("ca_membership_no", sa.String(50), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column(
            "last_login_at", sa.DateTime(timezone=True), nullable=True
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
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = LOWER(email)", name="ck_users_email_lowercase"),
        sa.CheckConstraint(
            r"phone IS NULL OR phone ~ '^[+]?[0-9]{10,15}$'",
            name="ck_users_phone_format",
        ),
    )
    op.create_index("idx_users_email", "users", ["email"])
    op.create_index(
        "idx_users_active",
        "users",
        ["is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.execute(
        "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ------------------------------------------------------------------
    # companies
    # ------------------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=True),
        sa.Column(
            "financial_year_start",
            sa.Date(),
            nullable=False,
            server_default=sa.text("'2026-04-01'::date"),
        ),
        sa.Column(
            "accounting_source",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'standalone'"),
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "active",
                "inactive",
                "suspended",
                name="company_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("pincode", sa.String(6), nullable=True),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.UniqueConstraint("gstin", name="uq_companies_gstin"),
        sa.CheckConstraint(
            r"gstin IS NULL OR gstin ~ "
            r"'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="ck_companies_gstin_format",
        ),
        sa.CheckConstraint(
            r"pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'",
            name="ck_companies_pan_format",
        ),
        sa.CheckConstraint(
            r"pincode IS NULL OR pincode ~ '^[0-9]{6}$'",
            name="ck_companies_pincode_format",
        ),
        sa.CheckConstraint(
            r"state_code IS NULL OR state_code ~ '^[0-9]{2}$'",
            name="ck_companies_state_code_format",
        ),
        sa.CheckConstraint(
            "EXTRACT(MONTH FROM financial_year_start) = 4 "
            "AND EXTRACT(DAY FROM financial_year_start) = 1",
            name="ck_companies_fy_start_april",
        ),
        sa.CheckConstraint(
            "accounting_source IN "
            "('standalone', 'tally', 'zoho', 'quickbooks', 'busy')",
            name="ck_companies_accounting_source",
        ),
    )
    op.create_index(
        "idx_companies_status",
        "companies",
        ["status"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "idx_companies_gstin",
        "companies",
        ["gstin"],
        postgresql_where=sa.text("gstin IS NOT NULL"),
    )
    op.execute(
        "CREATE TRIGGER trg_companies_updated_at BEFORE UPDATE ON companies "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ------------------------------------------------------------------
    # user_companies
    # ------------------------------------------------------------------
    op.create_table(
        "user_companies",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(
                "owner",
                "admin",
                "accountant",
                "viewer",
                name="company_role",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'viewer'"),
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
            "user_id", "company_id", name="uq_user_companies_user_company"
        ),
    )
    op.create_index("idx_user_companies_user", "user_companies", ["user_id"])
    op.create_index("idx_user_companies_company", "user_companies", ["company_id"])
    op.execute(
        "CREATE TRIGGER trg_user_companies_updated_at BEFORE UPDATE ON user_companies "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_user_companies_updated_at ON user_companies")
    op.drop_index("idx_user_companies_company", table_name="user_companies")
    op.drop_index("idx_user_companies_user", table_name="user_companies")
    op.drop_table("user_companies")

    op.execute("DROP TRIGGER IF EXISTS trg_companies_updated_at ON companies")
    op.drop_index("idx_companies_gstin", table_name="companies")
    op.drop_index("idx_companies_status", table_name="companies")
    op.drop_table("companies")

    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users")
    op.drop_index("idx_users_active", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS company_role")
    op.execute("DROP TYPE IF EXISTS company_status")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
