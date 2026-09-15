"""Opening-balance seed tracking (P3.2 implementation).

Adds the idempotency/reversibility markers the P3.2 architecture record
(`docs/PHASE_3_OPENING_BALANCE_ARCHITECTURE.md`) requires but does not by
itself introduce a per-FY opening field -- the correctness model there is
unchanged (single `ledgers.opening_balance` per ledger, anchored once).

* ``companies.opening_balance_anchor_date`` -- the anchor date the seed
  operation was run against. Set once on first seed; a later seed attempt
  with a *different* anchor is refused rather than silently re-anchoring.
* ``companies.opening_balance_seeded_at`` -- timestamp of the most recent
  seed attempt for this company (informational; updated on every run,
  including no-op re-runs).
* ``ledgers.opening_balance_seeded_at`` -- set the one time the seed
  operation writes a given ledger's opening balance. Distinguishes
  "never touched by the seed" (safe to seed) from "already seeded"
  (re-run is a no-op) and from a value that predates the seed, e.g. a
  Phase A direct-entry opening balance (never overwritten).

All three columns are nullable and additive; no backfill, no fabricated
values for existing rows.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("opening_balance_anchor_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "companies",
        sa.Column(
            "opening_balance_seeded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "ledgers",
        sa.Column(
            "opening_balance_seeded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ledgers", "opening_balance_seeded_at")
    op.drop_column("companies", "opening_balance_seeded_at")
    op.drop_column("companies", "opening_balance_anchor_date")
