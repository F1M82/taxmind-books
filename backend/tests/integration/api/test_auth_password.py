"""Integration tests for POST /api/v1/auth/password (P0.15)."""

from __future__ import annotations

from app.core.security import verify_password
from app.models.audit_log import AuditLog
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import issue_token, make_user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_change_password_204_on_success(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, password="old-password-12")
    r = client.post(
        "/api/v1/auth/password",
        headers=_bearer(issue_token(user)),
        json={
            "current_password": "old-password-12",
            "new_password": "new-password-12",
        },
    )
    assert r.status_code == 204
    assert r.text == ""

    db_session.expire_all()
    refreshed = db_session.query(User).filter(User.id == user.id).one()
    assert verify_password("new-password-12", refreshed.hashed_password)
    assert not verify_password("old-password-12", refreshed.hashed_password)


def test_change_password_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, password="old-password-12")
    r = client.post(
        "/api/v1/auth/password",
        headers=_bearer(issue_token(user)),
        json={
            "current_password": "old-password-12",
            "new_password": "new-password-12",
        },
    )
    assert r.status_code == 204
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "user",
            AuditLog.entity_id == user.id,
            AuditLog.action == "user.password_changed",
        )
        .one()
    )
    assert audit.user_id == user.id
    assert audit.company_id is None  # system event
    # The new password / hash MUST NOT be present in the audit row.
    new_value_text = str(audit.new_value)
    assert "new-password-12" not in new_value_text
    assert "$2b$" not in new_value_text


def test_change_password_wrong_current_is_401(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, password="old-password-12")
    r = client.post(
        "/api/v1/auth/password",
        headers=_bearer(issue_token(user)),
        json={
            "current_password": "wrong-password-12",
            "new_password": "new-password-12",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_change_password_short_new_is_422(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, password="old-password-12")
    r = client.post(
        "/api/v1/auth/password",
        headers=_bearer(issue_token(user)),
        json={
            "current_password": "old-password-12",
            "new_password": "short",
        },
    )
    assert r.status_code == 422


def test_change_password_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/password",
        json={
            "current_password": "anything-12345",
            "new_password": "anything-newer-12",
        },
    )
    assert r.status_code == 401
