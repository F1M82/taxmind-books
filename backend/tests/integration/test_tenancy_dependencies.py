"""End-to-end tests for the auth + tenancy dependency chain (P0.10).

Covers the contract from `docs/TENANCY.md`:

* `get_current_user`  — 401 on bad/expired tokens, on missing user,
  on inactive user; happy path returns the user.
* `get_active_company` — 422 when X-Company-ID header missing; 404
  (not 403) when user lacks membership or company suspended; happy
  path returns the company.
* `require_role`      — 403 when role doesn't match; happy path
  passes through.
* `get_scoped_session` — auto-injects ``WHERE company_id = X`` so a
  query against `Ledger` returns only the active company's rows even
  with no explicit filter.
"""

from __future__ import annotations

import time

import jwt
import pytest
from app.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.integration.conftest import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


# ---------------- get_current_user ----------------


def test_whoami_happy_path(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session)
    r = client.get(
        "/_probe/whoami",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == user.email


def test_whoami_no_auth_header(client: TestClient) -> None:
    r = client.get("/_probe/whoami")
    assert r.status_code == 401


def test_whoami_garbage_token(client: TestClient) -> None:
    r = client.get(
        "/_probe/whoami", headers={"Authorization": "Bearer not.a.token"}
    )
    assert r.status_code == 401


def test_whoami_expired_token(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session)
    cfg = get_settings()
    now = int(time.time()) - 10
    expired = jwt.encode(
        {"sub": str(user.id), "type": "access", "iat": now - 60, "exp": now},
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    r = client.get(
        "/_probe/whoami", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401


def test_whoami_refresh_token_rejected(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    refresh = create_refresh_token(user.id)
    r = client.get(
        "/_probe/whoami", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert r.status_code == 401


def test_whoami_inactive_user(client: TestClient, db_session: Session) -> None:
    user = make_user(db_session, is_active=False)
    r = client.get(
        "/_probe/whoami",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
    )
    assert r.status_code == 401


def test_whoami_unknown_user(client: TestClient) -> None:
    """Token for a UUID that has no matching user row → 401."""
    from uuid import uuid4

    token = create_access_token(uuid4())
    r = client.get("/_probe/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------------- get_active_company ----------------


def test_active_company_missing_header(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get(
        "/_probe/active-company",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
    )
    # FastAPI's automatic Header(...) validation → 422
    assert r.status_code == 422


def test_active_company_nonmember_returns_404(
    client: TestClient, db_session: Session
) -> None:
    """User U1 in company A; tries to use X-Company-ID for company B."""
    u1 = make_user(db_session, email="u1@example.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.viewer)
    # U1 is NOT a member of B.
    r = client.get(
        "/_probe/active-company",
        headers={
            "Authorization": f"Bearer {issue_token(u1)}",
            "X-Company-ID": str(b.id),
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_active_company_suspended_returns_404(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session, status="suspended")
    make_membership(db_session, u, c, role=CompanyRole.owner)
    r = client.get(
        "/_probe/active-company",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 404


def test_active_company_happy_path(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)
    r = client.get(
        "/_probe/active-company",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 200
    assert r.json()["id"] == str(c.id)


def test_active_company_unknown_id_returns_404(
    client: TestClient, db_session: Session
) -> None:
    """A UUID that isn't any company → 404 (no enumeration)."""
    from uuid import uuid4

    u = make_user(db_session)
    r = client.get(
        "/_probe/active-company",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(uuid4()),
        },
    )
    assert r.status_code == 404


# ---------------- require_role ----------------


def test_owner_only_passes_for_owner(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.owner)
    r = client.get(
        "/_probe/owner-only",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 200


def test_owner_only_rejects_viewer(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.viewer)
    r = client.get(
        "/_probe/owner-only",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 403


def test_owner_or_admin_accepts_admin(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.admin)
    r = client.get(
        "/_probe/owner-or-admin",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 200


def test_owner_or_admin_rejects_accountant(
    client: TestClient, db_session: Session
) -> None:
    u = make_user(db_session)
    c = make_company(db_session)
    make_membership(db_session, u, c, role=CompanyRole.accountant)
    r = client.get(
        "/_probe/owner-or-admin",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(c.id),
        },
    )
    assert r.status_code == 403


# ---------------- get_scoped_session ----------------


def _seed_ledger(db: Session, *, name: str, company_id) -> None:  # type: ignore[no-untyped-def]
    ledger = Ledger(
        company_id=company_id,
        name=name,
        name_normalized=name.lower(),
    )
    db.add(ledger)
    db.commit()


def test_scoped_session_filters_by_active_company(
    client: TestClient, db_session: Session
) -> None:
    """Auto-scope: a query for Ledgers returns only active-company rows."""
    u = make_user(db_session)
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u, a, role=CompanyRole.viewer)
    make_membership(db_session, u, b, role=CompanyRole.viewer)

    _seed_ledger(db_session, name="In-A-1", company_id=a.id)
    _seed_ledger(db_session, name="In-A-2", company_id=a.id)
    _seed_ledger(db_session, name="In-B-1", company_id=b.id)

    # Active company A → only In-A-* visible.
    r = client.get(
        "/_probe/scoped-list-ledgers",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(a.id),
        },
    )
    assert r.status_code == 200
    names = set(r.json()["names"])
    assert names == {"In-A-1", "In-A-2"}

    # Switch active company to B → only In-B-* visible.
    r = client.get(
        "/_probe/scoped-list-ledgers",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(b.id),
        },
    )
    assert r.status_code == 200
    assert set(r.json()["names"]) == {"In-B-1"}


def test_scoped_session_isolates_unmembered_company(
    client: TestClient, db_session: Session
) -> None:
    """A query inside the request can never see another tenant's data."""
    u = make_user(db_session)
    a = make_company(db_session, name="A")
    other = make_company(db_session, name="OTHER")
    make_membership(db_session, u, a, role=CompanyRole.viewer)

    _seed_ledger(db_session, name="In-A", company_id=a.id)
    _seed_ledger(db_session, name="In-OTHER", company_id=other.id)

    # User has no membership in `other`; using A as active company,
    # ensure the auto-scope filter blocks any cross-tenant read.
    r = client.get(
        "/_probe/scoped-list-ledgers",
        headers={
            "Authorization": f"Bearer {issue_token(u)}",
            "X-Company-ID": str(a.id),
        },
    )
    assert r.status_code == 200
    assert set(r.json()["names"]) == {"In-A"}
