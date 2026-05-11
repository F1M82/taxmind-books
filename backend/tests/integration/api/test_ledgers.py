"""Integration tests for /api/v1/ledgers/ (P0.17)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _setup(db_session: Session, role: CompanyRole = CompanyRole.owner):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=role)
    return user, company


# ---------------- Create ----------------


def test_create_ledger_201_minimal(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers=_h(user, company),
        json={"name": "Sharma Traders"},
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["name"] == "Sharma Traders"
    assert body["name_normalized"] == "sharma traders"
    assert body["balance_type"] == "Dr"
    assert Decimal(body["opening_balance"]) == Decimal("0.00")
    assert body["is_active"] is True


def test_create_ledger_full_payload(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers=_h(user, company),
        json={
            "name": "Bharat & Co.",
            "group_name": "Sundry Debtors",
            "opening_balance": "1500.50",
            "balance_type": "Cr",
            "gstin": "27BBBBB5678B1Z5",
            "pan": "ABCDE1234F",
            "phone": "+919876543210",
            "address": "Plot 42, MIDC, Nagpur",
            "state_code": "27",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["balance_type"] == "Cr"
    assert Decimal(body["opening_balance"]) == Decimal("1500.50")
    assert body["gstin"] == "27BBBBB5678B1Z5"


def test_create_ledger_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers=_h(user, company),
        json={"name": "Audit Co"},
    )
    assert r.status_code == 201
    lid = UUID(r.json()["id"])
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "ledger", AuditLog.entity_id == lid)
        .one()
    )
    assert audit.action == "ledger.created"
    assert audit.user_id == user.id
    assert audit.company_id == company.id


def test_create_ledger_invalid_gstin_422(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers=_h(user, company),
        json={"name": "Bad", "gstin": "invalid"},
    )
    assert r.status_code == 422


def test_create_ledger_money_rejects_float(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers=_h(user, company),
        json={"name": "Float", "opening_balance": 100.5},
    )
    assert r.status_code == 422


def test_create_ledger_requires_x_company_id(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post(
        "/api/v1/ledgers/",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
        json={"name": "Anything"},
    )
    assert r.status_code == 422  # Header(...) missing


# ---------------- List + fuzzy ----------------


def test_list_ledgers_default_active_only(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    db_session.add(
        Ledger(
            company_id=company.id,
            name="Active L",
            name_normalized="active l",
            is_active=True,
        )
    )
    db_session.add(
        Ledger(
            company_id=company.id,
            name="Inactive L",
            name_normalized="inactive l",
            is_active=False,
        )
    )
    db_session.commit()
    r = client.get("/api/v1/ledgers/", headers=_h(user, company))
    assert r.status_code == 200
    names = {item["name"] for item in r.json()["items"]}
    assert names == {"Active L"}


def test_list_ledgers_is_active_false_returns_inactive(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    db_session.add(
        Ledger(
            company_id=company.id,
            name="X",
            name_normalized="x",
            is_active=False,
        )
    )
    db_session.commit()
    r = client.get(
        "/api/v1/ledgers/?is_active=false",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    names = {item["name"] for item in r.json()["items"]}
    assert "X" in names


def test_list_ledgers_filter_by_group(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    for n, g in (("A", "Sundry Debtors"), ("B", "Sundry Creditors")):
        db_session.add(
            Ledger(
                company_id=company.id,
                name=n,
                name_normalized=n.lower(),
                group_name=g,
            )
        )
    db_session.commit()
    r = client.get(
        "/api/v1/ledgers/?group=Sundry+Debtors",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["name"] for i in items} == {"A"}


def test_list_ledgers_fuzzy_q_finds_typo(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    db_session.add(
        Ledger(
            company_id=company.id,
            name="Sharma Traders",
            name_normalized="sharma traders",
        )
    )
    db_session.commit()
    r = client.get(
        "/api/v1/ledgers/?q=sharma+trader",  # singular vs plural
        headers=_h(user, company),
    )
    assert r.status_code == 200
    names = {item["name"] for item in r.json()["items"]}
    assert "Sharma Traders" in names


# ---------------- Read ----------------


def test_get_ledger_404_when_other_company(
    client: TestClient, db_session: Session
) -> None:
    """A ledger belonging to a different company → 404 even though we
    have an active company on a route."""
    user, company = _setup(db_session)
    other = make_company(db_session, name="Other")
    other_ledger = Ledger(
        company_id=other.id,
        name="Other-only",
        name_normalized="other-only",
    )
    db_session.add(other_ledger)
    db_session.commit()
    r = client.get(
        f"/api/v1/ledgers/{other_ledger.id}",
        headers=_h(user, company),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ledger_not_found"


# ---------------- Update ----------------


def test_update_ledger_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    led = Ledger(
        company_id=company.id, name="Old", name_normalized="old"
    )
    db_session.add(led)
    db_session.commit()
    r = client.patch(
        f"/api/v1/ledgers/{led.id}",
        headers=_h(user, company),
        json={"name": "New"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "New"
    assert body["name_normalized"] == "new"
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "ledger",
            AuditLog.entity_id == led.id,
            AuditLog.action == "ledger.updated",
        )
        .one()
    )
    assert "name" in audit.changes


# ---------------- Delete (soft) ----------------


def test_delete_ledger_204_soft_deletes(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    led = Ledger(
        company_id=company.id, name="Goner", name_normalized="goner"
    )
    db_session.add(led)
    db_session.commit()
    r = client.delete(
        f"/api/v1/ledgers/{led.id}", headers=_h(user, company)
    )
    assert r.status_code == 204

    db_session.expire_all()
    refreshed = db_session.query(Ledger).filter(Ledger.id == led.id).one()
    assert refreshed.is_active is False
