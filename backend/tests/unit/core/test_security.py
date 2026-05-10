"""Unit tests for app.core.security."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from app.config import Settings
from app.core import security
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    BCRYPT_COST,
    REFRESH_TOKEN_TYPE,
    TokenExpired,
    TokenInvalid,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+psycopg://x:x@localhost/x",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="test-jwt-secret-x",
        SECRET_KEY="test-secret-key-x",
        CONNECTOR_JWT_SECRET="test-connector-secret-x",
        ACCESS_TOKEN_EXPIRE_MINUTES=30,
        REFRESH_TOKEN_EXPIRE_DAYS=14,
    )


# ---------------- Password hashing ----------------


def test_hash_password_returns_bcrypt_string() -> None:
    h = hash_password("hunter2")
    # bcrypt strings start with $2b$<cost>$ for the modern variant.
    assert h.startswith(f"$2b${BCRYPT_COST:02d}$")


def test_hash_password_is_non_deterministic() -> None:
    a = hash_password("hunter2")
    b = hash_password("hunter2")
    assert a != b  # different salts


def test_verify_password_accepts_correct() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True


def test_verify_password_rejects_wrong() -> None:
    h = hash_password("hunter2")
    assert verify_password("Hunter2", h) is False


def test_verify_password_rejects_garbage_hash() -> None:
    assert verify_password("anything", "not a bcrypt hash") is False


def test_verify_password_rejects_non_string() -> None:
    assert verify_password(b"bytes", "$2b$12$abc") is False  # type: ignore[arg-type]


def test_hash_password_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        hash_password(b"bytes")  # type: ignore[arg-type]


# ---------------- Access token ----------------


def test_create_access_token_has_required_claims() -> None:
    cfg = _settings()
    sub = uuid4()
    token = create_access_token(sub, settings=cfg)
    decoded = jwt.decode(
        token, cfg.JWT_SECRET.get_secret_value(), algorithms=[cfg.JWT_ALGORITHM]
    )
    assert decoded["sub"] == str(sub)
    assert decoded["type"] == ACCESS_TOKEN_TYPE
    assert "exp" in decoded
    assert "iat" in decoded


def test_access_token_default_expiry_30min(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _settings()
    token = create_access_token(uuid4(), settings=cfg)
    payload = decode_token(token, settings=cfg)
    delta = payload.exp - datetime.now(UTC)
    # Allow generous slack for test runtime.
    assert timedelta(minutes=29) <= delta <= timedelta(minutes=31)


def test_access_token_subject_can_be_string() -> None:
    cfg = _settings()
    token = create_access_token("user-1", settings=cfg)
    assert decode_token(token, settings=cfg).sub == "user-1"


# ---------------- Refresh token ----------------


def test_refresh_token_has_refresh_type() -> None:
    cfg = _settings()
    token = create_refresh_token(uuid4(), settings=cfg)
    payload = decode_token(token, settings=cfg)
    assert payload.type == REFRESH_TOKEN_TYPE


def test_refresh_token_distinguishable_from_access() -> None:
    cfg = _settings()
    access = create_access_token(uuid4(), settings=cfg)
    refresh = create_refresh_token(uuid4(), settings=cfg)
    assert decode_token(access, settings=cfg).type == ACCESS_TOKEN_TYPE
    assert decode_token(refresh, settings=cfg).type == REFRESH_TOKEN_TYPE


def test_refresh_token_expiry_14d() -> None:
    cfg = _settings()
    token = create_refresh_token(uuid4(), settings=cfg)
    payload = decode_token(token, settings=cfg)
    delta = payload.exp - datetime.now(UTC)
    assert timedelta(days=13, hours=23) <= delta <= timedelta(days=14, hours=1)


# ---------------- decode_token ----------------


def test_decode_token_returns_payload() -> None:
    cfg = _settings()
    sub = uuid4()
    token = create_access_token(sub, settings=cfg)
    payload = decode_token(token, settings=cfg)
    assert payload.sub == str(sub)
    assert payload.type == ACCESS_TOKEN_TYPE
    assert isinstance(payload.exp, datetime)
    assert payload.raw["sub"] == str(sub)


def test_decode_token_rejects_wrong_type() -> None:
    cfg = _settings()
    refresh = create_refresh_token(uuid4(), settings=cfg)
    with pytest.raises(TokenInvalid) as exc_info:
        decode_token(refresh, expected_type=ACCESS_TOKEN_TYPE, settings=cfg)
    assert "expected access" in str(exc_info.value)


def test_decode_token_rejects_expired() -> None:
    cfg = _settings()
    # Hand-craft a token with iat/exp in the past.
    now = int(time.time()) - 10
    expired = jwt.encode(
        {
            "sub": "user-1",
            "type": ACCESS_TOKEN_TYPE,
            "iat": now - 60,
            "exp": now,
        },
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(TokenExpired):
        decode_token(expired, settings=cfg)


def test_decode_token_rejects_bad_signature() -> None:
    cfg = _settings()
    token = create_access_token(uuid4(), settings=cfg)
    # Flip the last char of the signature segment.
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}.{sig[:-1]}{('A' if sig[-1] != 'A' else 'B')}"
    with pytest.raises(TokenInvalid):
        decode_token(tampered, settings=cfg)


def test_decode_token_rejects_garbage() -> None:
    cfg = _settings()
    with pytest.raises(TokenInvalid):
        decode_token("not.a.token", settings=cfg)


def test_decode_token_rejects_missing_sub() -> None:
    cfg = _settings()
    bad = jwt.encode(
        {"type": ACCESS_TOKEN_TYPE, "exp": int(time.time()) + 60},
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(TokenInvalid) as exc_info:
        decode_token(bad, settings=cfg)
    assert "sub" in str(exc_info.value)


def test_decode_token_rejects_missing_type() -> None:
    cfg = _settings()
    bad = jwt.encode(
        {"sub": "user-1", "exp": int(time.time()) + 60},
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )
    with pytest.raises(TokenInvalid) as exc_info:
        decode_token(bad, settings=cfg)
    assert "type" in str(exc_info.value)


# ---------------- Extra claims ----------------


def test_extra_claims_are_passed_through() -> None:
    cfg = _settings()
    token = create_access_token(
        "u1", extra_claims={"company_id": "c1"}, settings=cfg
    )
    payload = decode_token(token, settings=cfg)
    assert payload.raw["company_id"] == "c1"


def test_extra_claims_cannot_override_reserved_names() -> None:
    cfg = _settings()
    token = create_access_token(
        "u1",
        extra_claims={"sub": "spoofed", "type": "spoofed"},
        settings=cfg,
    )
    payload = decode_token(token, settings=cfg)
    assert payload.sub == "u1"
    assert payload.type == ACCESS_TOKEN_TYPE


# ---------------- Constants ----------------


def test_bcrypt_cost_is_12() -> None:
    assert BCRYPT_COST == 12


def test_module_does_not_log_secrets() -> None:
    """Sanity: the module source contains no `logger.info(secret)` etc."""
    src = (
        __import__("inspect").getsource(security)
    )
    for needle in ("token)", "password)", "JWT_SECRET)"):
        assert f"logger.info({needle}" not in src
        assert f"print({needle}" not in src
