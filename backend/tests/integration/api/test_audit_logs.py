"""Integration tests for GET /api/v1/audit-logs/ (P0.20)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _seed_audit(
    db,  # type: ignore[no-untyped-def]
    *,
    company_id,
    user_id,
    action: str = "voucher.created",
    entity_type: str = "voucher",
    entity_id=None,  # type: ignore[no-untyped-def]
    when: datetime | None = None,
):
    log = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id or uuid4(),
        new_value={"foo": "bar"},
        source="api",
    )
    if when is not None:
        log.created_at = when
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _setup(db_session: Session, role: CompanyRole = CompanyRole.owner):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=role)
    return user, company


# ---------------- Role gate ----------------


def test_owner_can_list(client: TestClient, db_session: Session) -> None:
    user, company = _setup(db_session, role=CompanyRole.owner)
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id
    )
    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 200


def test_admin_can_list(client: TestClient, db_session: Session) -> None:
    user, company = _setup(db_session, role=CompanyRole.admin)
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id
    )
    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 200


def test_accountant_403(client: TestClient, db_session: Session) -> None:
    user, company = _setup(db_session, role=CompanyRole.accountant)
    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_role"


def test_viewer_403(client: TestClient, db_session: Session) -> None:
    user, company = _setup(db_session, role=CompanyRole.viewer)
    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 403


# ---------------- Filters ----------------


def test_lists_only_active_company(
    client: TestClient, db_session: Session
) -> None:
    user, a = _setup(db_session)
    b = make_company(db_session, name="B")
    make_membership(db_session, user, b, role=CompanyRole.owner)
    _seed_audit(db_session, company_id=a.id, user_id=user.id)
    _seed_audit(db_session, company_id=b.id, user_id=user.id)
    r = client.get("/api/v1/audit-logs/", headers=_h(user, a))
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_filter_by_entity_type(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    _seed_audit(db_session, company_id=company.id, user_id=user.id,
                action="voucher.created", entity_type="voucher")
    _seed_audit(db_session, company_id=company.id, user_id=user.id,
                action="ledger.created", entity_type="ledger")
    r = client.get(
        "/api/v1/audit-logs/?entity_type=ledger",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    types = {item["entity_type"] for item in r.json()["items"]}
    assert types == {"ledger"}


def test_filter_by_entity_id(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    target = uuid4()
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id, entity_id=target
    )
    _seed_audit(db_session, company_id=company.id, user_id=user.id)
    r = client.get(
        f"/api/v1/audit-logs/?entity_id={target}",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_filter_by_action(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id,
        action="voucher.created"
    )
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id,
        action="voucher.updated"
    )
    r = client.get(
        "/api/v1/audit-logs/?action=voucher.updated",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    actions = {item["action"] for item in r.json()["items"]}
    assert actions == {"voucher.updated"}


def test_filter_by_date_range(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    now = datetime.now(UTC)
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id,
        when=now - timedelta(days=10),
    )
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id,
        when=now - timedelta(days=2),
    )
    target_day = (now - timedelta(days=2)).date().isoformat()
    r = client.get(
        f"/api/v1/audit-logs/?from={target_day}&to={target_day}",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_user_email_resolved(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    _seed_audit(
        db_session, company_id=company.id, user_id=user.id
    )
    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["user_email"] == user.email


def test_system_event_rows_excluded(
    client: TestClient, db_session: Session
) -> None:
    """user.created / password_changed / etc. have company_id = NULL.

    They must NOT appear in the tenant-scoped read API.
    """
    user, company = _setup(db_session)
    # Tenant-scoped row in this company.
    _seed_audit(db_session, company_id=company.id, user_id=user.id)
    # System event (e.g. user.created at registration).
    sys_log = AuditLog(
        company_id=None,
        user_id=user.id,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        new_value={"email": user.email},
        source="api",
    )
    db_session.add(sys_log)
    db_session.commit()

    r = client.get("/api/v1/audit-logs/", headers=_h(user, company))
    assert r.status_code == 200
    # Only the tenant-scoped row.
    assert r.json()["meta"]["total"] == 1
    actions = {item["action"] for item in r.json()["items"]}
    assert "user.created" not in actions
