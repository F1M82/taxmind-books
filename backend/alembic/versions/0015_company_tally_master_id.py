"""company tally master id

Adds ``companies.tally_master_id`` (the Tally company GUID) as the durable
external identity for a local company bound to a Tally company (Phase 7B).

Safe for production: the column is nullable and no GUID is fabricated for
existing rows. The global unique constraint only governs non-NULL values
(PostgreSQL treats NULLs as distinct), so legacy companies that predate
Tally identity capture keep ``tally_master_id IS NULL`` and do not collide.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("tally_master_id", sa.String(100), nullable=True),
    )
    op.create_unique_constraint(
        "uq_companies_tally_master_id", "companies", ["tally_master_id"]
    )
    op.create_index(
        "idx_companies_tally_master_id",
        "companies",
        ["tally_master_id"],
        postgresql_where=sa.text("tally_master_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_companies_tally_master_id", table_name="companies")
    op.drop_constraint(
        "uq_companies_tally_master_id", "companies", type_="unique"
    )
    op.drop_column("companies", "tally_master_id")
