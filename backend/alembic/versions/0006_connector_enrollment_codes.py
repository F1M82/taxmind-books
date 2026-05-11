"""connector_enrollment_codes table (v1.2 Patch 2)

Stores the one-time codes issued by `POST /api/v1/connector/
enrollment-codes` (owner-only) and consumed by
`POST /api/v1/connector/enroll`. The code itself is a random
URL-safe token; only its SHA-256 hash is persisted (exfiltrated DB
shouldn't grant the attacker live codes).

CONNECTOR_PROTOCOL.md describes the enrollment flow; SCHEMA.sql
didn't model the storage. See AMENDMENTS_v1.2 §"Patch 2".

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_enrollment_codes",
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
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # SHA-256 hex of the raw code; never store the code itself.
        sa.Column(
            "code_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "code_hash", name="uq_connector_enrollment_codes_hash"
        ),
    )
    op.create_index(
        "idx_connector_enrollment_codes_company",
        "connector_enrollment_codes",
        ["company_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_connector_enrollment_codes_pending",
        "connector_enrollment_codes",
        ["expires_at"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_connector_enrollment_codes_pending",
        table_name="connector_enrollment_codes",
    )
    op.drop_index(
        "idx_connector_enrollment_codes_company",
        table_name="connector_enrollment_codes",
    )
    op.drop_table("connector_enrollment_codes")
