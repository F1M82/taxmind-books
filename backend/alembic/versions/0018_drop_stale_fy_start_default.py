"""Drop the frozen 2026-04-01 server_default on companies.financial_year_start.

The ORM now always supplies an explicit value at insert time (either the
real value from Tally's STARTINGFROM, or a computed "current FY start"
fallback -- see app.models.company._default_financial_year_start), so the
old hardcoded database-level default is now dead weight that would only
ever fire on a raw INSERT bypassing the ORM, silently producing a wrong
date. Better to have that fail loudly (NOT NULL violation) than succeed
with a stale guess.
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("companies", "financial_year_start", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "companies",
        "financial_year_start",
        server_default=sa.text("'2026-04-01'::date"),
    )
