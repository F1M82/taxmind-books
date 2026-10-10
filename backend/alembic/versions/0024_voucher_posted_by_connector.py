"""voucher `posted_by_connector_id` (v1.3 item 8, attribution only)

Records which connector (which PC) actually posted a voucher to Tally.
No FK -- connectors come and go, see CLAUDE.md / AMENDMENTS_v1.3.md. The
same connector_id is also stamped on the posting audit row.

Multi-connector *concurrent* dispatch (the other half of item 8) is not
addressed here: the registry (`ConnectorRegistry`) only ever holds one
live connection per company_id -- a second connector reconnecting
supersedes the first -- so two connectors can never race to post the
same voucher today. This column covers attribution only.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column(
            "posted_by_connector_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("vouchers", "posted_by_connector_id")
