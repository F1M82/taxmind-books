"""ingestions table (Phase 1 wedge: invoice-scan extraction pipeline)

Per docs/EXTRACTION_CONTRACT.md + docs/SCHEMA.sql section 7. Flow B
(v1.2): no separate draft_vouchers table -- an extracted invoice lands
directly in `vouchers` with is_optional_in_tally=true once a party
ledger is resolved. `vouchers.source_ingestion_id` /
`vouchers.is_auto_posted` / `vouchers.confidence_score` already exist
(migration 0003); this migration only adds the ingestion row itself.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE ingestion_source AS ENUM "
        "('whatsapp', 'sms', 'email', 'photo', 'pdf', 'csv', 'voice', "
        "'manual', 'tally_sync')"
    )
    op.execute(
        "CREATE TYPE ingestion_status AS ENUM "
        "('received', 'extracting', 'extracted', 'matching', 'matched', "
        "'review', 'posted', 'failed', 'discarded')"
    )

    op.create_table(
        "ingestions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source",
            postgresql.ENUM(
                "whatsapp",
                "sms",
                "email",
                "photo",
                "pdf",
                "csv",
                "voice",
                "manual",
                "tally_sync",
                name="ingestion_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "received",
                "extracting",
                "extracted",
                "matching",
                "matched",
                "review",
                "posted",
                "failed",
                "discarded",
                name="ingestion_status",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'received'"),
        ),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column("s3_object_key", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("content_size", sa.BigInteger(), nullable=True),
        sa.Column("sender_identifier", sa.String(255), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column(
            "posted_voucher_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vouchers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("parsed_data", postgresql.JSONB(), nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "extraction_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::JSONB"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
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
            "company_id",
            "idempotency_key",
            name="uq_ingestions_company_idempotency",
        ),
        sa.CheckConstraint(
            "content_size IS NULL OR content_size > 0",
            name="ck_ingestions_size_positive",
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_ingestions_extraction_confidence",
        ),
    )
    op.create_index(
        "idx_ingestions_company_status", "ingestions", ["company_id", "status"]
    )
    op.create_index(
        "idx_ingestions_company_received",
        "ingestions",
        ["company_id", sa.text("received_at DESC")],
    )
    op.create_index(
        "idx_ingestions_pending",
        "ingestions",
        ["status"],
        postgresql_where=sa.text(
            "status IN ('received', 'extracting', 'matching')"
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_ingestions_updated_at "
        "BEFORE UPDATE ON ingestions "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ingestions_updated_at ON ingestions")
    op.drop_index("idx_ingestions_pending", table_name="ingestions")
    op.drop_index("idx_ingestions_company_received", table_name="ingestions")
    op.drop_index("idx_ingestions_company_status", table_name="ingestions")
    op.drop_table("ingestions")
    op.execute("DROP TYPE ingestion_status")
    op.execute("DROP TYPE ingestion_source")
