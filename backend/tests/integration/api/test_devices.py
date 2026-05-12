"""Integration tests for /api/v1/devices/* (P0.44)."""

from __future__ import annotations

from uuid import uuid4

from app.models.audit_log import AuditLog
from app.models.device_token import DeviceToken
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests._db_fixtures import issue_token, make_user


def _auth(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {issue_token(user)}"}


# ---------------------------------------------------------------------
# register
# ---------------------------------------------------------------------


def test_register_creates_active_row_and_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)

    r = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={
            "token": "fcm-token-1",
            "platform": "android",
            "app_version": "1.0.0",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_registered"] is True

    row = db_session.scalar(
        select(DeviceToken).where(DeviceToken.token == "fcm-token-1")
    )
    assert row is not None
    assert row.user_id == user.id
    assert row.platform.value == "android"
    assert row.is_active is True
    assert row.app_version == "1.0.0"

    # Audit row: action=device.registered, entity_id=row.id.
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "device.registered",
            AuditLog.entity_id == row.id,
        )
    )
    assert audit is not None
    assert audit.user_id == user.id


def test_register_is_idempotent_for_same_token(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)

    r1 = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={"token": "fcm-token-dup", "platform": "android"},
    )
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    # Re-register same token → same id, still active.
    r2 = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={
            "token": "fcm-token-dup",
            "platform": "android",
            "app_version": "1.0.1",
        },
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id

    # Exactly one row in the table.
    count = (
        db_session.execute(
            select(DeviceToken).where(DeviceToken.token == "fcm-token-dup")
        )
        .scalars()
        .all()
    )
    assert len(count) == 1
    row = count[0]
    assert row.app_version == "1.0.1"
    assert row.is_active is True


def test_register_reactivates_after_unregister(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)

    r = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={"token": "fcm-token-r", "platform": "ios"},
    )
    device_id = r.json()["id"]

    client.delete(f"/api/v1/devices/{device_id}", headers=_auth(user))

    r2 = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={"token": "fcm-token-r", "platform": "ios"},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == device_id

    row = db_session.scalar(
        select(DeviceToken).where(DeviceToken.id == device_id)
    )
    assert row is not None
    assert row.is_active is True


def test_register_reassigns_token_when_a_different_user_takes_it(
    client: TestClient, db_session: Session
) -> None:
    user_a = make_user(db_session, email="a@example.com")
    user_b = make_user(db_session, email="b@example.com")

    r1 = client.post(
        "/api/v1/devices/register",
        headers=_auth(user_a),
        json={"token": "shared-device-token", "platform": "android"},
    )
    device_id = r1.json()["id"]

    r2 = client.post(
        "/api/v1/devices/register",
        headers=_auth(user_b),
        json={"token": "shared-device-token", "platform": "android"},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == device_id

    row = db_session.scalar(
        select(DeviceToken).where(DeviceToken.id == device_id)
    )
    assert row is not None
    assert row.user_id == user_b.id


def test_register_rejects_unauthenticated(client: TestClient) -> None:
    r = client.post(
        "/api/v1/devices/register",
        json={"token": "x", "platform": "android"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------
# unregister
# ---------------------------------------------------------------------


def test_unregister_deactivates_and_audits(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={"token": "to-unreg", "platform": "web"},
    )
    device_id = r.json()["id"]

    r2 = client.delete(
        f"/api/v1/devices/{device_id}", headers=_auth(user)
    )
    assert r2.status_code == 204, r2.text

    row = db_session.scalar(
        select(DeviceToken).where(DeviceToken.id == device_id)
    )
    assert row is not None
    assert row.is_active is False

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "device.unregistered",
            AuditLog.entity_id == row.id,
        )
    )
    assert audit is not None


def test_unregister_returns_204_when_already_inactive(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post(
        "/api/v1/devices/register",
        headers=_auth(user),
        json={"token": "double-unreg", "platform": "android"},
    )
    device_id = r.json()["id"]
    client.delete(f"/api/v1/devices/{device_id}", headers=_auth(user))

    r2 = client.delete(
        f"/api/v1/devices/{device_id}", headers=_auth(user)
    )
    assert r2.status_code == 204


def test_unregister_404s_for_other_users_device(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session, email="owner@example.com")
    intruder = make_user(db_session, email="intruder@example.com")
    r = client.post(
        "/api/v1/devices/register",
        headers=_auth(owner),
        json={"token": "owners-token", "platform": "android"},
    )
    device_id = r.json()["id"]

    r2 = client.delete(
        f"/api/v1/devices/{device_id}", headers=_auth(intruder)
    )
    assert r2.status_code == 404


def test_unregister_404s_for_unknown_id(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.delete(
        f"/api/v1/devices/{uuid4()}", headers=_auth(user)
    )
    assert r.status_code == 404
