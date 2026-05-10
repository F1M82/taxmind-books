"""Integration tests for POST /api/v1/auth/refresh (P0.15)."""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
from app.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests._db_fixtures import make_user


def test_refresh_with_valid_token_returns_new_pair(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    old_refresh = create_refresh_token(user.id)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.json()
    body = r.json()
    decode_token(body["access_token"], expected_type="access")
    decode_token(body["refresh_token"], expected_type="refresh")
    assert body["user"]["email"] == user.email


def test_refresh_rejects_access_token(
    client: TestClient, db_session: Session
) -> None:
    """An access token presented to /refresh must 401, not silently work."""
    user = make_user(db_session)
    access = create_access_token(user.id)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_refresh_rejects_expired_token(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    cfg = get_settings()
    now = int(time.time()) - 10
    expired = jwt.encode(
        {"sub": str(user.id), "type": "refresh", "iat": now - 60, "exp": now},
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": expired})
    assert r.status_code == 401


def test_refresh_rejects_garbage(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not.a.token"}
    )
    assert r.status_code == 401


def test_refresh_for_unknown_user_id_is_401(client: TestClient) -> None:
    """Refresh token signed for a user that doesn't exist any more."""
    refresh = create_refresh_token(uuid4())
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


def test_refresh_for_inactive_user_is_401(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session, is_active=False)
    refresh = create_refresh_token(user.id)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


def test_refresh_missing_field_is_422(client: TestClient) -> None:
    r = client.post("/api/v1/auth/refresh", json={})
    assert r.status_code == 422
