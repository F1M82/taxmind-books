from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from app.schemas.common import TaxMindBooksBase


class TallyCompanyDiscoveryOut(TaxMindBooksBase):
    discovery_id: UUID
    tally_company_identifier: str
    tally_company_name: str
    tally_master_id: str | None = None
    gstin: str | None = None
    financial_year_start: date | None = None
    mapped_to_backend_company_id: UUID | None = None


class TallyCompaniesOut(TaxMindBooksBase):
    connector_id: UUID
    tally_data_folder_path: str | None = None
    scanned_at: datetime | None = None
    companies: list[TallyCompanyDiscoveryOut]


class ActiveTallyCompanyOut(TaxMindBooksBase):
    connector_id: UUID
    tally_company_identifier: str | None = None
    tally_company_name: str | None = None
    tally_master_id: str | None = None


class TallyMappingRequest(TaxMindBooksBase):
    discovery_id: UUID


class TallyMappingOut(TaxMindBooksBase):
    company_id: UUID
    connector_id: UUID
    tally_data_folder_path: str
    tally_company_identifier: str
    tally_company_display_name: str
    tally_mapping_configured_at: datetime
    tally_mapping_configured_by: UUID | None = None
