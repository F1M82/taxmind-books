"""Persisted connector identity and connector/company bindings."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[UUID] = uuid_pk()
    enrolled_company_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    data_folder_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (Index("idx_connectors_enrolled_company", "enrolled_company_id"),)


class TallyCompanyDiscovery(Base):
    __tablename__ = "tally_companies_discovered"

    id: Mapped[UUID] = uuid_pk()
    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    data_folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    tally_company_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    tally_master_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tally_company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    financial_year_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("connector_id", "data_folder_path", "tally_company_identifier", name="uq_tally_discovery_ref"),
        Index("idx_tally_discovery_connector", "connector_id", "scanned_at"),
    )


class ConnectorCompanyBinding(Base):
    __tablename__ = "connector_company_bindings"

    id: Mapped[UUID] = uuid_pk()
    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    data_folder_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    tally_company_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    tally_master_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tally_company_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    configured_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    configured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("connector_id", "company_id", name="uq_connector_company_binding"),
        UniqueConstraint("connector_id", "data_folder_path", "tally_company_identifier", name="uq_connector_binding_reference"),
        Index("idx_connector_bindings_company", "company_id"),
    )
