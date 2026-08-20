"""Focused P3.8 persisted discovery and trusted mapping coverage."""

from __future__ import annotations

from uuid import uuid4

from app.core.audit import AuditContext, AuditEmitter
from app.models.company import CompanyRole
from app.models.connector import Connector, ConnectorCompanyBinding, TallyCompanyDiscovery
from app.services.tally.discovery_service import ingest_discovery

from tests._db_fixtures import issue_token, make_company, make_membership, make_user


def _headers(user, company):  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {issue_token(user)}", "X-Company-ID": str(company.id)}


def test_discovery_is_persisted_and_reference_mapping_is_authorized(client, db_session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    connector = Connector(enrolled_company_id=company.id)
    db_session.add(connector)
    db_session.commit()
    db_session.refresh(connector)
    audit = AuditEmitter(db_session, AuditContext(company=company, user=user, ip_address=None,
        user_agent="test", request_id=uuid4(), source="api"))
    ingest_discovery(db_session, connector_id=connector.id, data_folder_path="C:/Tally/Data",
        companies=[{"tally_company_identifier": "10000", "tally_master_id": "GUID-10000", "tally_company_name": "Acme Traders"}], audit=audit)
    db_session.commit()

    listed = client.get(f"/api/v1/connector/{connector.id}/tally-companies", headers=_headers(user, company))
    assert listed.status_code == 200, listed.json()
    assert listed.json()["companies"][0]["tally_company_identifier"] == "10000"
    discovery_id = listed.json()["companies"][0]["discovery_id"]

    mapped = client.post("/api/v1/connector/tally-mapping", headers=_headers(user, company), json={
        "discovery_id": discovery_id,
    })
    assert mapped.status_code == 200, mapped.json()
    assert mapped.json()["tally_company_display_name"] == "Acme Traders"
    assert db_session.query(ConnectorCompanyBinding).count() == 1
    db_session.refresh(company)
    binding = db_session.query(ConnectorCompanyBinding).one()
    assert company.tally_master_id == "GUID-10000"
    assert binding.tally_master_id == "GUID-10000"


def test_mapping_rejects_untrusted_discovery_reference(client, db_session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    connector = Connector(enrolled_company_id=company.id)
    db_session.add(connector)
    db_session.commit()
    db_session.refresh(connector)
    response = client.post("/api/v1/connector/tally-mapping", headers=_headers(user, company), json={
        "discovery_id": str(uuid4()),
    })
    assert response.status_code == 404
    assert db_session.query(TallyCompanyDiscovery).count() == 0


def test_provisioning_company_can_authorize_mapping_to_second_company(
    client, db_session
):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    provisioning = make_company(db_session)
    target = make_company(db_session)
    make_membership(db_session, user, provisioning, role=CompanyRole.owner)
    make_membership(db_session, user, target, role=CompanyRole.owner)
    connector = Connector(enrolled_company_id=provisioning.id)
    db_session.add(connector)
    db_session.commit()
    db_session.refresh(connector)
    audit = AuditEmitter(
        db_session,
        AuditContext(
            company=provisioning,
            user=user,
            ip_address=None,
            user_agent="test",
            request_id=uuid4(),
            source="api",
        ),
    )
    ingest_discovery(
        db_session,
        connector_id=connector.id,
        data_folder_path="C:/Tally/Data",
        companies=[
            {
                "tally_company_identifier": "10000",
                "tally_master_id": "GUID-10000",
                "tally_company_name": "Acme Traders",
            }
        ],
        audit=audit,
    )
    db_session.commit()
    discovery = db_session.query(TallyCompanyDiscovery).one()

    first = client.post(
        "/api/v1/connector/tally-mapping",
        headers=_headers(user, provisioning),
        json={"discovery_id": str(discovery.id)},
    )
    assert first.status_code == 200, first.json()

    collision = client.post(
        "/api/v1/connector/tally-mapping",
        headers=_headers(user, target),
        json={"discovery_id": str(discovery.id)},
    )
    assert collision.status_code == 409, collision.json()
