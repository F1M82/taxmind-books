"""Company-level timezone column.

Fixes the "dashboard 'today' computed in UTC, not IST" gap recorded in
`release/TAXMIND-PILOT-READINESS-2026-08-12.md` §18/§20: for ~5.5h every
night (00:00-05:29 IST), day-boundary math anchored to UTC labels the
prior IST day's data as "today," affecting every India user.

Adds `companies.timezone` (IANA name, default ``'Asia/Kolkata'`` -- correct
for every existing company today, since TaxMind Books is India-only). Safe
for production: additive, NOT NULL with a server default, so existing rows
backfill to the correct value with no manual data migration.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'Asia/Kolkata'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "timezone")
