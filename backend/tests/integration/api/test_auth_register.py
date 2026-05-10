"""Integration tests for POST /api/v1/auth/register (P0.14)."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.core.security import verify_password
from app.models.audit_log import AuditLog
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "email": "Alice@Example.com",
        "password": "correct-horse-battery",
        "full_name": "Alice Anand",
        "phone": "+919876543210",
        "is_ca": True,
        "firm_name": "Anand & Co",
        "ca_membership_no": "M123456",
    }
    base.update(overrides)
    return base


def test_register_returns_201_with_user_payload(
    client: TestClient, db_session: Session
) -> None:
    r = client.post("/api/v1/auth/register", json=_payload())
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["email"] == "alice@example.com"  # lowercased
    assert body["full_name"] == "Alice Anand"
    assert body["is_ca"] is True
    assert body["firm_name"] == "Anand & Co"
    assert body["is_active"] is True
    UUID(body["id"])
    # `password` and `phone` and `hashed_password` MUST NOT leak.
    for forbidden in ("password", "hashed_password", "phone"):
        assert forbidden not in body


def test_register_lowercases_email_in_db(
    client: TestClient, db_session: Session
) -> None:
    r = client.post("/api/v1/auth/register", json=_payload(email="MixED@Case.IO"))
    assert r.status_code == 201
    user = db_session.query(User).filter(User.email == "mixed@case.io").one()
    assert user.email == "mixed@case.io"


def test_register_bcrypt_hashes_the_password(
    client: TestClient, db_session: Session
) -> None:
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(email="hash@example.com", password="correcthorsebattery!"),
    )
    assert r.status_code == 201
    user = (
        db_session.query(User).filter(User.email == "hash@example.com").one()
    )
    assert user.hashed_password.startswith("$2b$12$")
    assert verify_password("correcthorsebattery!", user.hashed_password)


def test_register_writes_user_created_audit_log(
    client: TestClient, db_session: Session
) -> None:
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(email="audit@example.com"),
    )
    assert r.status_code == 201
    user_id = UUID(r.json()["id"])

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "user",
            AuditLog.entity_id == user_id,
            AuditLog.action == "user.created",
        )
        .one()
    )
    assert audit.company_id is None  # system event
    assert audit.user_id == user_id  # the new user is the actor
    assert audit.source == "api"
    assert audit.new_value is not None
    assert audit.new_value["email"] == "audit@example.com"
    # No password / hash leaked into the audit row.
    assert "hashed_password" not in audit.new_value
    assert "password" not in audit.new_value


def test_register_audit_log_actor_is_the_new_user(
    client: TestClient, db_session: Session
) -> None:
    """Self-registration: the new user is recorded as the audit actor."""
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(email="self-actor@example.com"),
    )
    assert r.status_code == 201
    user_id = UUID(r.json()["id"])
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == user_id)
        .one()
    )
    assert audit.user_id == user_id


def test_register_duplicate_email_returns_409(
    client: TestClient, db_session: Session
) -> None:
    r1 = client.post(
        "/api/v1/auth/register",
        json=_payload(email="dup@example.com"),
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/api/v1/auth/register",
        json=_payload(email="DUP@example.com"),  # different case, same email
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "email_already_registered"


def test_register_short_password_is_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(password="short1"),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_register_malformed_phone_is_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(phone="not-a-phone"),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_register_missing_email_is_422(client: TestClient) -> None:
    payload = _payload()
    payload.pop("email")
    r = client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 422


def test_register_optional_fields_default_correctly(
    client: TestClient, db_session: Session
) -> None:
    """phone, firm_name, ca_membership_no default to NULL when omitted."""
    payload = _payload(
        email="minimal@example.com",
        is_ca=False,
    )
    payload.pop("phone")
    payload.pop("firm_name")
    payload.pop("ca_membership_no")
    r = client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 201, r.json()
    user = (
        db_session.query(User).filter(User.email == "minimal@example.com").one()
    )
    assert user.phone is None
    assert user.firm_name is None
    assert user.ca_membership_no is None
    assert user.is_ca is False
    assert user.is_active is True
    assert user.is_superuser is False


@pytest.mark.parametrize("password_len", [11, 0])
def test_register_password_below_min_length(
    client: TestClient, password_len: int
) -> None:
    r = client.post(
        "/api/v1/auth/register",
        json=_payload(password="x" * password_len),
    )
    assert r.status_code == 422
