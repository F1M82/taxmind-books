"""Persistence and trusted-reference operations for Tally discovery."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import Conflict, NotFound
from app.models.company import Company
from app.models.connector import Connector, ConnectorCompanyBinding, TallyCompanyDiscovery


class DiscoveryReferenceNotFound(NotFound):
    code = "tally_company_not_found_in_discovery"


class DiscoveryMappingConflict(Conflict):
    code = "tally_mapping_collision"


def ingest_discovery(
    db: Session,
    *,
    connector_id: UUID,
    data_folder_path: str,
    companies: list[dict[str, Any]],
    audit: AuditEmitter,
) -> list[TallyCompanyDiscovery]:
    connector = db.query(Connector).filter(Connector.id == connector_id).first()
    if connector is None:
        raise DiscoveryReferenceNotFound("Connector not found.")
    now = datetime.now(UTC)
    connector.data_folder_path = data_folder_path
    connector.last_seen_at = now
    rows: list[TallyCompanyDiscovery] = []
    for item in companies:
        identifier = str(item.get("tally_company_identifier") or "").strip()
        name = str(item.get("tally_company_name") or "").strip()
        tally_master_id = str(item.get("tally_master_id") or item.get("tally_company_guid") or "").strip() or None
        if not identifier or not name:
            continue
        fy = item.get("financial_year_start")
        fy_date = date.fromisoformat(fy) if isinstance(fy, str) and fy else None
        row = db.query(TallyCompanyDiscovery).filter(
            TallyCompanyDiscovery.connector_id == connector_id,
            TallyCompanyDiscovery.data_folder_path == data_folder_path,
            TallyCompanyDiscovery.tally_company_identifier == identifier,
        ).first()
        if row is None:
            row = TallyCompanyDiscovery(
                connector_id=connector_id,
                data_folder_path=data_folder_path,
                tally_company_identifier=identifier,
                tally_master_id=tally_master_id,
                tally_company_name=name,
                gstin=item.get("gstin"),
                financial_year_start=fy_date,
                scanned_at=now,
            )
            db.add(row)
        else:
            row.tally_company_name = name
            if tally_master_id:
                row.tally_master_id = tally_master_id
            row.gstin = item.get("gstin")
            row.financial_year_start = fy_date
            row.scanned_at = now
        rows.append(row)
    audit.emit(
        action="connector.tally_companies_discovered",
        entity_type="connector",
        entity_id=connector_id,
        old_value=None,
        new_value={"data_folder_path": data_folder_path, "count": len(rows)},
    )
    db.flush()
    return rows


def bind_discovery_reference(
    db: Session,
    *,
    company: Company,
    discovery_id: UUID,
    user_id: UUID,
    audit: AuditEmitter,
) -> ConnectorCompanyBinding:
    discovery = db.query(TallyCompanyDiscovery).filter(TallyCompanyDiscovery.id == discovery_id).first()
    if discovery is None:
        raise DiscoveryReferenceNotFound("Tally company was not found in discovery.")
    if not discovery.tally_master_id:
        raise DiscoveryReferenceNotFound("Discovery record has no authoritative Tally GUID.")
    connector_id = discovery.connector_id
    data_folder_path = discovery.data_folder_path
    identifier = discovery.tally_company_identifier
    tally_master_id = discovery.tally_master_id
    company_conflict = db.query(Company).filter(
        Company.tally_master_id == tally_master_id, Company.id != company.id
    ).first()
    if company_conflict is not None:
        raise DiscoveryMappingConflict("Tally company is already mapped to another company.")
    conflict = db.query(ConnectorCompanyBinding).filter(
        ConnectorCompanyBinding.connector_id == connector_id,
        ConnectorCompanyBinding.data_folder_path == data_folder_path,
        ConnectorCompanyBinding.tally_company_identifier == identifier,
        ConnectorCompanyBinding.company_id != company.id,
    ).first()
    if conflict is not None:
        raise DiscoveryMappingConflict("Tally company is already mapped to another company.")
    existing = db.query(ConnectorCompanyBinding).filter(
        ConnectorCompanyBinding.connector_id == connector_id,
        ConnectorCompanyBinding.company_id == company.id,
    ).first()
    if company.tally_master_id and company.tally_master_id != tally_master_id:
        raise DiscoveryMappingConflict("Company is already mapped to another Tally company.")
    old = None if existing is None else {"tally_master_id": company.tally_master_id}
    company.tally_master_id = tally_master_id
    if existing is None:
        existing = ConnectorCompanyBinding(
            connector_id=connector_id, company_id=company.id,
            data_folder_path=data_folder_path, tally_company_identifier=identifier,
            tally_master_id=tally_master_id,
            tally_company_display_name=discovery.tally_company_name,
            configured_by=user_id, configured_at=datetime.now(UTC),
        )
        db.add(existing)
        action = "company.tally_mapping_configured"
    else:
        existing.data_folder_path = data_folder_path
        existing.tally_company_identifier = identifier
        existing.tally_master_id = tally_master_id
        existing.tally_company_display_name = discovery.tally_company_name
        existing.configured_by = user_id
        existing.configured_at = datetime.now(UTC)
        action = "company.tally_mapping_changed"
    audit.emit(action=action, entity_type="company", entity_id=company.id,
               old_value=old, new_value={"tally_master_id": tally_master_id, "connector_id": connector_id})
    db.flush()
    return existing
