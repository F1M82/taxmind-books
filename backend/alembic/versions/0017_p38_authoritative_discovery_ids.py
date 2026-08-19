"""Add authoritative Tally GUIDs to persisted discovery and bindings."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tally_companies_discovered", sa.Column("tally_master_id", sa.String(100), nullable=True))
    op.add_column("connector_company_bindings", sa.Column("tally_master_id", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("connector_company_bindings", "tally_master_id")
    op.drop_column("tally_companies_discovered", "tally_master_id")
