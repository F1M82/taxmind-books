"""P3.8 persisted connectors, discovery records, and bindings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table("connectors",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("enrolled_company_id", uuid, sa.ForeignKey("companies.id", ondelete="SET NULL")),
        sa.Column("data_folder_path", sa.String(1024)), sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_connectors_enrolled_company", "connectors", ["enrolled_company_id"])
    op.create_table("tally_companies_discovered",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connector_id", uuid, sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data_folder_path", sa.String(1024), nullable=False),
        sa.Column("tally_company_identifier", sa.String(255), nullable=False),
        sa.Column("tally_company_name", sa.String(255), nullable=False), sa.Column("gstin", sa.String(15)),
        sa.Column("financial_year_start", sa.Date()), sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "data_folder_path", "tally_company_identifier", name="uq_tally_discovery_ref"),
    )
    op.create_index("idx_tally_discovery_connector", "tally_companies_discovered", ["connector_id", "scanned_at"])
    op.create_table("connector_company_bindings",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connector_id", uuid, sa.ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", uuid, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data_folder_path", sa.String(1024), nullable=False), sa.Column("tally_company_identifier", sa.String(255), nullable=False),
        sa.Column("tally_company_display_name", sa.String(255), nullable=False),
        sa.Column("configured_by", uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connector_id", "company_id", name="uq_connector_company_binding"),
        sa.UniqueConstraint("connector_id", "data_folder_path", "tally_company_identifier", name="uq_connector_binding_reference"),
    )
    op.create_index("idx_connector_bindings_company", "connector_company_bindings", ["company_id"])


def downgrade() -> None:
    op.drop_table("connector_company_bindings")
    op.drop_table("tally_companies_discovered")
    op.drop_index("idx_connectors_enrolled_company", table_name="connectors")
    op.drop_table("connectors")
