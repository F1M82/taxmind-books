"""Tenant-isolation tests for /audit-logs/ (P0.20)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.models.audit_log import AuditLog
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


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _seed(db, *, company_id, user_id, action="voucher.created"):  # type: ignore[no-untyped-def]
    log = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_type="voucher",
        entity_id=uuid4(),
        new_value={"x": 1},
        source="api",
    )
    db.add(log)
    db.commit()


def test_user_cannot_see_other_companys_audit_logs(
    client: TestClient, db_session: Session
) -> None:
    u1 = make_user(db_session, email="u1@ex.com")
    u2 = make_user(db_session, email="u2@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)
    make_membership(db_session, u2, b, role=CompanyRole.owner)

    _seed(db_session, company_id=a.id, user_id=u1.id)
    _seed(db_session, company_id=b.id, user_id=u2.id)

    r = client.get("/api/v1/audit-logs/", headers=_h(u1, a))
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_user_cannot_use_other_companys_id_via_header(
    client: TestClient, db_session: Session
) -> None:
    """User in A; tries to set X-Company-ID=B → 404 from
    get_active_company before the role check fires."""
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    _seed(db_session, company_id=b.id, user_id=u1.id)
    r = client.get("/api/v1/audit-logs/", headers=_h(u1, b))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_filter_by_user_id_does_not_leak_other_company_rows(
    client: TestClient, db_session: Session
) -> None:
    """user_id filter doesn't punch through tenant scoping."""
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)
    make_membership(db_session, u1, b, role=CompanyRole.owner)

    # u1 has rows in both a and b.
    _seed(db_session, company_id=a.id, user_id=u1.id)
    _seed(db_session, company_id=b.id, user_id=u1.id)
    _seed(db_session, company_id=b.id, user_id=u1.id)

    # Query with X-Company-ID=A and user_id=u1 — should only see A's rows.
    r = client.get(
        f"/api/v1/audit-logs/?user_id={u1.id}",
        headers=_h(u1, a),
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1
