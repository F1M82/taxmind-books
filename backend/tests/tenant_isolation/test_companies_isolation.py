"""Tenant-isolation tests for /companies/ (P0.16).

Per `docs/TENANCY.md` failure-mode tests:

  1. User U1 (member of A only) cannot read company B by ID → 404
  2. U1 cannot list B's data — list returns only A
  3. U1 cannot mutate B — 404 (NOT 403, no enumeration leak)
  4. U1 cannot add a member to B — 404
"""

from __future__ import annotations

import pytest
from app.models.company import CompanyRole
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


pytestmark = pytest.mark.tenant_isolation


def _h(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_user_cannot_read_other_companys_data(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)
    # u1 is NOT a member of B.

    r = client.get(f"/api/v1/companies/{b.id}", headers=_h(u1))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_list_does_not_leak_other_tenants_companies(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    u2 = make_user(db_session, email="u2@ex.com")
    a = make_company(db_session, name="A-only")
    b = make_company(db_session, name="B-only")
    make_membership(db_session, u1, a, role=CompanyRole.owner)
    make_membership(db_session, u2, b, role=CompanyRole.owner)

    r = client.get("/api/v1/companies/", headers=_h(u1))
    assert r.status_code == 200
    names = {item["name"] for item in r.json()["items"]}
    assert "B-only" not in names
    assert names == {"A-only"}


def test_user_cannot_patch_other_companys_data(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session)
    b = make_company(db_session, name="B")
    # u1 has no membership of any company.
    r = client.patch(
        f"/api/v1/companies/{b.id}",
        headers=_h(u1),
        json={"name": "Hijacked"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"

    db_session.expire_all()
    db_session.refresh(b)
    assert b.name == "B"  # unchanged


def test_user_cannot_add_member_to_other_company(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    target = make_user(db_session, email="target@ex.com")
    b = make_company(db_session)
    # u1 is NOT a member of B.
    r = client.post(
        f"/api/v1/companies/{b.id}/members",
        headers=_h(u1),
        json={"email": "target@ex.com", "role": "viewer"},
    )
    # 404 (anti-enumeration) NOT 403.
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_user_cannot_set_company_id_via_path_to_bypass(
    client: TestClient, db_session: Session
) -> None:
    """Even if the URL points at B, the membership check uses (user, B)."""
    u1 = make_user(db_session)
    b = make_company(db_session, name="B")
    r = client.get(f"/api/v1/companies/{b.id}", headers=_h(u1))
    assert r.status_code == 404
