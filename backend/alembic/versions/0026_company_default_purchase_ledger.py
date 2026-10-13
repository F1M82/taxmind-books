"""company `default_purchase_ledger_id` (Phase 1 wedge auto-voucher decision)

The non-party (Dr) side of an auto-posted Purchase voucher built from an
extracted invoice. Per-company, single ledger -- the smallest of the
options raised in CLAUDE.md "Phase 1 wedge" / docs/API.md "What's NOT
built yet"; a per-category mapping is a bigger feature for later if this
proves too coarse. Nullable: auto-voucher construction stays off
(ingestions land in `matched`/`review` same as before) until an owner/
admin sets it via `PATCH /companies/{id}`.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "default_purchase_ledger_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledgers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "default_purchase_ledger_id")
