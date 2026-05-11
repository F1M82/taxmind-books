"""Unit tests for the User model.

These tests are pure-Python (no DB). They verify the SQLAlchemy table
definition matches docs/SCHEMA.sql — column types, constraints,
indexes. The DB-side enforcement (CHECK regexes, triggers, defaults) is
covered by integration tests.
"""

from __future__ import annotations

from app.models.user import User
from sqlalchemy import Boolean, DateTime, String


def test_user_tablename() -> None:
    assert User.__tablename__ == "users"


def test_user_has_expected_columns() -> None:
    cols = {c.name for c in User.__table__.columns}
    assert cols == {
        "id",
        "email",
        "hashed_password",
        "full_name",
        "phone",
        "is_ca",
        "firm_name",
        "ca_membership_no",
        "is_active",
        "is_superuser",
        "last_login_at",
        "created_at",
        "updated_at",
    }


def test_user_email_is_string_255_not_null() -> None:
    col = User.__table__.columns["email"]
    assert isinstance(col.type, String)
    assert col.type.length == 255
    assert col.nullable is False


def test_user_phone_is_nullable() -> None:
    col = User.__table__.columns["phone"]
    assert col.nullable is True


def test_user_boolean_columns() -> None:
    for name in ("is_ca", "is_active", "is_superuser"):
        col = User.__table__.columns[name]
        assert isinstance(col.type, Boolean)
        assert col.nullable is False


def test_user_timestamp_columns() -> None:
    for name in ("created_at", "updated_at"):
        col = User.__table__.columns[name]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True
        assert col.nullable is False


def test_user_last_login_at_nullable() -> None:
    col = User.__table__.columns["last_login_at"]
    assert col.nullable is True


def test_user_has_email_unique_constraint() -> None:
    constraint_names = {c.name for c in User.__table__.constraints}
    assert "uq_users_email" in constraint_names


def test_user_has_check_constraints() -> None:
    names = {c.name for c in User.__table__.constraints if c.name is not None}
    assert "ck_users_email_lowercase" in names
    assert "ck_users_phone_format" in names


def test_user_has_indexes() -> None:
    names = {ix.name for ix in User.__table__.indexes}
    assert "idx_users_email" in names
    assert "idx_users_active" in names


def test_user_repr() -> None:
    u = User(
        email="alice@example.com",
        hashed_password="x",  # placeholder, not a real credential
        full_name="Alice",
    )
    s = repr(u)
    assert "alice@example.com" in s
