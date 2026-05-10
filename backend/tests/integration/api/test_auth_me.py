"""Integration tests for GET /api/v1/auth/me (P0.15)."""

from __future__ import annotations

from app.models.company import CompanyRole
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_me_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_returns_user_with_no_companies(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, email="solo@example.com")
    r = client.get("/api/v1/auth/me", headers=_bearer(issue_token(user)))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "solo@example.com"
    assert body["companies"] == []


def test_me_lists_company_memberships(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, email="multi@example.com")
    a = make_company(db_session, name="Acme")
    b = make_company(db_session, name="Beta")
    make_membership(db_session, user, a, role=CompanyRole.owner)
    make_membership(db_session, user, b, role=CompanyRole.admin)

    r = client.get("/api/v1/auth/me", headers=_bearer(issue_token(user)))
    assert r.status_code == 200
    body = r.json()
    by_name = {c["name"]: c for c in body["companies"]}
    assert {"Acme", "Beta"}.issubset(by_name)
    assert by_name["Acme"]["role"] == "owner"
    assert by_name["Beta"]["role"] == "admin"
    assert len(body["companies"]) == 2


def test_me_does_not_require_x_company_id(
    client: TestClient, db_session: Session
) -> None:
    """No X-Company-ID needed; /me is per-user, not per-tenant."""
    user = make_user(db_session)
    r = client.get("/api/v1/auth/me", headers=_bearer(issue_token(user)))
    assert r.status_code == 200


def test_me_does_not_leak_password(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get("/api/v1/auth/me", headers=_bearer(issue_token(user)))
    assert "hashed_password" not in r.text
    assert "password" not in r.json()
