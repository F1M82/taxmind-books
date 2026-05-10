"""Integration tests for POST /api/v1/auth/login (P0.15)."""

from __future__ import annotations

from app.core.security import decode_token
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests._db_fixtures import make_user


def test_login_with_correct_credentials_returns_tokens(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, email="alice@example.com", password="hunter22-pwd")
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "alice@example.com", "password": "hunter22-pwd"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    decode_token(body["access_token"], expected_type="access")
    decode_token(body["refresh_token"], expected_type="refresh")
    assert body["user"]["email"] == "alice@example.com"


def test_login_email_is_case_insensitive(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, email="bob@example.com", password="hunter22-pwd")
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "Bob@Example.COM", "password": "hunter22-pwd"},
    )
    assert r.status_code == 200


def test_login_wrong_password_is_401(
    client: TestClient, db_session: Session
) -> None:
    make_user(db_session, email="charlie@example.com", password="rightpass-12")
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "charlie@example.com", "password": "wrongpass-12"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_is_401_not_404(
    client: TestClient, db_session: Session
) -> None:
    """Don't distinguish unknown user from wrong password (per API.md)."""
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "ghost@example.com", "password": "anything-12345"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_login_inactive_user_is_403(
    client: TestClient, db_session: Session
) -> None:
    make_user(
        db_session,
        email="inactive@example.com",
        password="rightpass-12345",
        is_active=False,
    )
    r = client.post(
        "/api/v1/auth/login",
        data={
            "username": "inactive@example.com",
            "password": "rightpass-12345",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "user_inactive"


def test_login_updates_last_login_at(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, email="ll@example.com", password="hunter22-pwd")
    assert user.last_login_at is None
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "ll@example.com", "password": "hunter22-pwd"},
    )
    assert r.status_code == 200
    db_session.expire_all()
    refreshed = db_session.query(User).filter(User.email == "ll@example.com").one()
    assert refreshed.last_login_at is not None
