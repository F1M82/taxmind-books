"""audit_logs (append-only) + idempotency_keys

Adds the cross-cutting tables: audit_logs (every mutation, never
mutated; UPDATE/DELETE blocked at the DB by `prevent_audit_modification()`)
and idempotency_keys (request deduplication, CASCADE on company delete).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # audit_logs
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
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
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column(
            "entity_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("old_value", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("new_value", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "changes",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::JSONB"),
        ),
        sa.Column("ip_address", sa.dialects.postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "request_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "source IN ('api', 'worker', 'connector', 'system')",
            name="ck_audit_logs_source",
        ),
    )

    op.create_index(
        "idx_audit_logs_company_created",
        "audit_logs",
        ["company_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_audit_logs_entity",
        "audit_logs",
        ["entity_type", "entity_id"],
    )
    op.create_index(
        "idx_audit_logs_user",
        "audit_logs",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "idx_audit_logs_request",
        "audit_logs",
        ["request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # Append-only enforcement (Layer 2 from AUDIT.md)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_modification() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only — UPDATE and DELETE are forbidden';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification()"
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification()"
    )

    # ------------------------------------------------------------------
    # idempotency_keys
    # ------------------------------------------------------------------
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_body", sa.dialects.postgresql.JSONB(), nullable=True
        ),
        sa.Column(
            "response_headers", sa.dialects.postgresql.JSONB(), nullable=True
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.UniqueConstraint(
            "company_id", "key", name="uq_idempotency_keys_company_key"
        ),
    )

    op.create_index(
        "idx_idempotency_keys_expires", "idempotency_keys", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "idx_idempotency_keys_expires", table_name="idempotency_keys"
    )
    op.drop_table("idempotency_keys")

    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification() CASCADE")

    op.drop_index("idx_audit_logs_request", table_name="audit_logs")
    op.drop_index("idx_audit_logs_user", table_name="audit_logs")
    op.drop_index("idx_audit_logs_entity", table_name="audit_logs")
    op.drop_index("idx_audit_logs_company_created", table_name="audit_logs")
    op.drop_table("audit_logs")
