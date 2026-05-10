"""Tenant-isolation tests for /ledgers/ (P0.17)."""

from __future__ import annotations

import pytest
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


pytestmark = pytest.mark.tenant_isolation


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _seed(db: Session, company, name: str) -> Ledger:  # type: ignore[no-untyped-def]
    led = Ledger(
        company_id=company.id, name=name, name_normalized=name.lower()
    )
    db.add(led)
    db.commit()
    db.refresh(led)
    return led


def test_user_cannot_read_ledger_in_other_company(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    led_in_b = _seed(db_session, b, "B-Ledger")
    r = client.get(f"/api/v1/ledgers/{led_in_b.id}", headers=_h(u1, a))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ledger_not_found"


def test_list_does_not_leak_other_companys_ledgers(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)
    _seed(db_session, a, "L-In-A")
    _seed(db_session, b, "L-In-B")

    r = client.get("/api/v1/ledgers/", headers=_h(u1, a))
    assert r.status_code == 200
    names = {item["name"] for item in r.json()["items"]}
    assert names == {"L-In-A"}


def test_user_cannot_patch_ledger_in_other_company(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    led_in_b = _seed(db_session, b, "B-Ledger")
    r = client.patch(
        f"/api/v1/ledgers/{led_in_b.id}",
        headers=_h(u1, a),
        json={"name": "Hijacked"},
    )
    assert r.status_code == 404
    db_session.expire_all()
    db_session.refresh(led_in_b)
    assert led_in_b.name == "B-Ledger"


def test_user_cannot_delete_ledger_in_other_company(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    led_in_b = _seed(db_session, b, "B-Ledger")
    r = client.delete(f"/api/v1/ledgers/{led_in_b.id}", headers=_h(u1, a))
    assert r.status_code == 404
    db_session.expire_all()
    db_session.refresh(led_in_b)
    assert led_in_b.is_active is True


def test_x_company_id_header_must_match_resource(
    client: TestClient, db_session: Session
) -> None:
    """A user member of both A and B uses A's header on a B ledger.

    The route resolves the active company from the header. The ledger
    is queried with `company_id = active`. Mismatch → 404.
    """
    user = make_user(db_session)
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, user, a, role=CompanyRole.owner)
    make_membership(db_session, user, b, role=CompanyRole.owner)

    led_in_b = _seed(db_session, b, "B-Ledger")
    # Header says "active company is A" — ledger is in B → 404.
    r = client.get(f"/api/v1/ledgers/{led_in_b.id}", headers=_h(user, a))
    assert r.status_code == 404

    # Switch X-Company-ID to B → visible.
    r2 = client.get(f"/api/v1/ledgers/{led_in_b.id}", headers=_h(user, b))
    assert r2.status_code == 200
