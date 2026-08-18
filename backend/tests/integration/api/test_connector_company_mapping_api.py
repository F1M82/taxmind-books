"""P3.7 Phase 7B — API-level tests for the company Tally-GUID mapping endpoint.

The service-level suite (test_company_mapping.py) proves the mapping logic
against a raw session. These tests exercise the *actual HTTP route*
``POST /api/v1/connector/company-mapping/confirm`` through the full FastAPI
stack — auth, X-Company-ID resolution, role gate, request parsing, service,
commit, audit — and verify the persistence boundary directly against the DB:
a successful confirmation must leave ``Company.tally_master_id`` set AND an
audit row committed in the same transaction.

Diagnostic purpose: prove the endpoint writes to the same database/session
when legitimately invoked, so a missing live mapping can only be an operator
action (never invoked), not an endpoint defect.
"""

from __future__ import annotations

from app.models.audit_log import AuditLog
from app.models.company import Company, CompanyRole
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    make_company,
    make_membership,
    make_user,
)

GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"
OTHER_GUID = "d41b0ee6-5fc5-4fdc-a10e-bd489d5423b9"


def _headers(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    from tests._db_fixtures import issue_token

    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _mapping_count(db: Session, company_id, action: str) -> int:  # type: ignore[no-untyped-def]
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.company_id == company_id,
            AuditLog.action == action,
        )
        .count()
    )


def test_confirm_endpoint_persists_company_and_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Vighnaharta Agro Chemicals")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    before = (
        db_session.query(Company)
        .filter(Company.id == company.id)
        .one()
    )
    assert before.tally_master_id is None

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={
            "tally_company_guid": GUID,
            "tally_company_name": "Vighnaharta Agro Chemicals - FROM 1-APR-2025",
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["company_id"] == str(company.id)
    assert body["tally_master_id"] == GUID

    # The same database/session must hold both the Company row change and the
    # audit row — read them freshly from the DB via a new query.
    db_session.expire_all()
    persisted = (
        db_session.query(Company)
        .filter(Company.id == company.id)
        .one()
    )
    assert persisted.tally_master_id == GUID

    assert (
        _mapping_count(db_session, company.id, "company.tally_mapping_configured")
        == 1
    )
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "company.tally_mapping_configured",
        )
        .one()
    )
    assert audit.new_value is not None
    assert audit.new_value["tally_master_id"] == GUID
    assert audit.old_value == {"tally_master_id": None}


def test_confirm_endpoint_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(
        db_session, name="Acme", tally_master_id=GUID
    )
    make_membership(db_session, user, company, role=CompanyRole.owner)

    r1 = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={"tally_company_guid": GUID},
    )
    assert r1.status_code == 200, r1.json()

    r2 = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={"tally_company_guid": GUID},
    )
    assert r2.status_code == 200, r2.json()

    # No duplicate audit rows and no failed writes.
    assert (
        _mapping_count(db_session, company.id, "company.tally_mapping_configured")
        == 0
    )
    rows = (
        db_session.query(Company)
        .filter(Company.id == company.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].tally_master_id == GUID


def test_confirm_endpoint_rejects_mismatched_guid(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(
        db_session, name="Acme", tally_master_id=GUID
    )
    make_membership(db_session, user, company, role=CompanyRole.owner)

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={"tally_company_guid": OTHER_GUID},
    )
    assert r.status_code == 409, r.json()

    db_session.expire_all()
    persisted = (
        db_session.query(Company)
        .filter(Company.id == company.id)
        .one()
    )
    assert persisted.tally_master_id == GUID  # unchanged


def test_confirm_endpoint_requires_owner_role(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.accountant)

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={"tally_company_guid": GUID},
    )
    assert r.status_code == 403, r.json()


def test_confirm_endpoint_rejects_guid_bound_elsewhere(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    other = make_company(db_session, name="Other", tally_master_id=GUID)
    target = make_company(db_session, name="Target")
    make_membership(db_session, user, target, role=CompanyRole.owner)

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, target),
        json={"tally_company_guid": GUID},
    )
    assert r.status_code == 409, r.json()

    db_session.expire_all()
    assert other.tally_master_id == GUID
    target_row = (
        db_session.query(Company)
        .filter(Company.id == target.id)
        .one()
    )
    assert target_row.tally_master_id is None


def test_mapping_status_endpoint_reflects_confirmed_mapping(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    before = client.get(
        "/api/v1/connector/company-mapping",
        headers=_headers(user, company),
    )
    assert before.status_code == 200, before.json()
    assert before.json()["mapped"] is False

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        headers=_headers(user, company),
        json={"tally_company_guid": GUID},
    )
    assert r.status_code == 200, r.json()

    after = client.get(
        "/api/v1/connector/company-mapping",
        headers=_headers(user, company),
    )
    assert after.status_code == 200, after.json()
    assert after.json()["mapped"] is True
    assert after.json()["tally_master_id"] == GUID


def test_confirm_endpoint_requires_auth(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    r = client.post(
        "/api/v1/connector/company-mapping/confirm",
        json={"tally_company_guid": GUID},
    )
    assert r.status_code == 401, r.json()
