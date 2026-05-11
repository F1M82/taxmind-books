"""Integration tests for /api/v1/companies/ (P0.16)."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.models.audit_log import AuditLog
from app.models.company import CompanyRole, UserCompany
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _company_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Acme Traders",
        "gstin": "27AAAAA1234A1Z5",
        "pan": "AAAAA1234A",
        "financial_year_start": "2026-04-01",
        "address": "123 Main St",
        "city": "Nagpur",
        "state_code": "27",
        "pincode": "440001",
        "accounting_source": "tally",
    }
    base.update(overrides)
    return base


# ---------------- Create ----------------


def test_create_company_201_creator_is_owner(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post("/api/v1/companies/", headers=_h(user), json=_company_payload())
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["name"] == "Acme Traders"
    assert body["status"] == "active"
    assert body["your_role"] == "owner"
    UUID(body["id"])

    # Membership row exists with owner role.
    db_session.expire_all()
    membership = (
        db_session.query(UserCompany)
        .filter(
            UserCompany.user_id == user.id,
            UserCompany.company_id == UUID(body["id"]),
        )
        .one()
    )
    assert membership.role == CompanyRole.owner


def test_create_company_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post("/api/v1/companies/", headers=_h(user), json=_company_payload())
    assert r.status_code == 201
    cid = UUID(r.json()["id"])
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "company",
            AuditLog.entity_id == cid,
            AuditLog.action == "company.created",
        )
        .one()
    )
    assert audit.user_id == user.id
    assert audit.company_id == cid


def test_create_company_duplicate_gstin_409(
    client: TestClient, db_session: Session
) -> None:
    a = make_user(db_session, email="a@ex.com")
    b = make_user(db_session, email="b@ex.com")
    r1 = client.post("/api/v1/companies/", headers=_h(a), json=_company_payload())
    assert r1.status_code == 201
    r2 = client.post(
        "/api/v1/companies/",
        headers=_h(b),
        json=_company_payload(name="Other Co"),
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "gstin_already_registered"


def test_create_company_invalid_gstin_422(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post(
        "/api/v1/companies/",
        headers=_h(user),
        json=_company_payload(gstin="invalid"),
    )
    assert r.status_code == 422


def test_create_company_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/companies/", json=_company_payload())
    assert r.status_code == 401


# ---------------- Get ----------------


def test_get_company_returns_with_role(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.admin)
    r = client.get(f"/api/v1/companies/{company.id}", headers=_h(user))
    assert r.status_code == 200
    body = r.json()
    assert body["your_role"] == "admin"
    assert body["name"] == "Acme"


def test_get_company_404_when_not_member(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    # No membership.
    r = client.get(f"/api/v1/companies/{company.id}", headers=_h(user))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_get_company_unknown_id_is_404(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get(f"/api/v1/companies/{uuid4()}", headers=_h(user))
    assert r.status_code == 404


# ---------------- List ----------------


def test_list_companies_returns_only_users_companies(
    client: TestClient, db_session: Session
) -> None:
    alice = make_user(db_session, email="alice@ex.com")
    bob = make_user(db_session, email="bob@ex.com")
    a1 = make_company(db_session, name="A1")
    a2 = make_company(db_session, name="A2")
    b1 = make_company(db_session, name="B1")
    make_membership(db_session, alice, a1, role=CompanyRole.owner)
    make_membership(db_session, alice, a2, role=CompanyRole.admin)
    make_membership(db_session, bob, b1, role=CompanyRole.owner)

    r = client.get("/api/v1/companies/", headers=_h(alice))
    assert r.status_code == 200
    body = r.json()
    names = {item["name"] for item in body["items"]}
    assert names == {"A1", "A2"}
    assert body["meta"]["total"] == 2


def test_list_companies_empty_for_user_with_none(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get("/api/v1/companies/", headers=_h(user))
    assert r.status_code == 200
    assert r.json() == {"items": [], "meta": {"next_cursor": None, "total": 0}}


# ---------------- Update ----------------


def test_update_company_requires_owner_or_admin(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.viewer)
    r = client.patch(
        f"/api/v1/companies/{company.id}",
        headers=_h(user),
        json={"name": "New Name"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_role"


def test_update_company_admin_can_update(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.admin)
    r = client.patch(
        f"/api/v1/companies/{company.id}",
        headers=_h(user),
        json={"name": "New Name"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"


def test_update_company_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Old")
    make_membership(db_session, user, company, role=CompanyRole.owner)
    r = client.patch(
        f"/api/v1/companies/{company.id}",
        headers=_h(user),
        json={"name": "New"},
    )
    assert r.status_code == 200
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "company",
            AuditLog.entity_id == company.id,
            AuditLog.action == "company.settings_updated",
        )
        .one()
    )
    assert audit.changes == {"name": ["Old", "New"]}


def test_update_company_404_when_not_member(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    r = client.patch(
        f"/api/v1/companies/{company.id}",
        headers=_h(user),
        json={"name": "New"},
    )
    assert r.status_code == 404


# ---------------- Members ----------------


def test_add_member_owner_can_add(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session, email="owner@ex.com")
    invitee = make_user(db_session, email="acc@ex.com")
    company = make_company(db_session)
    make_membership(db_session, owner, company, role=CompanyRole.owner)

    r = client.post(
        f"/api/v1/companies/{company.id}/members",
        headers=_h(owner),
        json={"email": "acc@ex.com", "role": "accountant"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user_email"] == "acc@ex.com"
    assert body["role"] == "accountant"
    assert body["user_id"] == str(invitee.id)


def test_add_member_admin_cannot_add_owner_only(
    client: TestClient, db_session: Session
) -> None:
    """Per API.md: members endpoint requires owner role specifically."""
    admin = make_user(db_session, email="admin@ex.com")
    make_user(db_session, email="other@ex.com")
    company = make_company(db_session)
    make_membership(db_session, admin, company, role=CompanyRole.admin)

    r = client.post(
        f"/api/v1/companies/{company.id}/members",
        headers=_h(admin),
        json={"email": "other@ex.com", "role": "viewer"},
    )
    assert r.status_code == 403


def test_add_member_unknown_email_is_404(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, owner, company, role=CompanyRole.owner)
    r = client.post(
        f"/api/v1/companies/{company.id}/members",
        headers=_h(owner),
        json={"email": "ghost@nowhere.io", "role": "viewer"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_not_found"


def test_add_member_already_member_409(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session)
    invitee = make_user(db_session, email="existing@ex.com")
    company = make_company(db_session)
    make_membership(db_session, owner, company, role=CompanyRole.owner)
    make_membership(db_session, invitee, company, role=CompanyRole.viewer)
    r = client.post(
        f"/api/v1/companies/{company.id}/members",
        headers=_h(owner),
        json={"email": "existing@ex.com", "role": "admin"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_member"


def test_add_member_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session)
    make_user(db_session, email="audit-mem@ex.com")
    company = make_company(db_session)
    make_membership(db_session, owner, company, role=CompanyRole.owner)
    r = client.post(
        f"/api/v1/companies/{company.id}/members",
        headers=_h(owner),
        json={"email": "audit-mem@ex.com", "role": "viewer"},
    )
    assert r.status_code == 201
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "user_company",
            AuditLog.action == "user_company.role_assigned",
            AuditLog.company_id == company.id,
        )
        .one()
    )
    assert audit.user_id == owner.id
    assert audit.new_value["user_email"] == "audit-mem@ex.com"
    assert audit.new_value["role"] == "viewer"
