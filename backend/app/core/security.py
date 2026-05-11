"""Password hashing + JWT utilities.

bcrypt for passwords (cost 12, per OWASP Authentication Cheat Sheet).
PyJWT for access + refresh tokens.

`create_access_token` and `create_refresh_token` differ only in their
`type` claim and expiry; `decode_token` returns a `TokenPayload` and
raises subclasses of `TokenError` on every failure mode (expired,
malformed, signature mismatch). Callers catch `TokenError` and map to
HTTP 401 in the auth dependency layer (P0.10).

The module never logs token payloads or secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import bcrypt
import jwt

from app.config import Settings, get_settings

# bcrypt's `gensalt(rounds=...)` value. 12 is the OWASP-recommended
# minimum for bcrypt as of 2024-2026; higher costs are slower without
# meaningfully changing security against modern hardware.
BCRYPT_COST = 12

# `type` claim values — distinguish access from refresh.
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

TokenType = Literal["access", "refresh"]


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------


class TokenError(Exception):
    """Base for every JWT decode failure."""


class TokenExpired(TokenError):
    pass


class TokenInvalid(TokenError):
    pass


# ---------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt (cost 12).

    bcrypt's wire format silently truncates inputs longer than 72 bytes
    (it has always done so) — by hashing the password ourselves we can
    keep an unbounded input length. We don't, since the registration
    schema caps password length far below that, and uniformity with
    bcrypt's reference behavior matters more than gaining a few bytes.
    """
    if not isinstance(plain, str):
        raise TypeError("password must be str")
    salt = bcrypt.gensalt(rounds=BCRYPT_COST)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    if not isinstance(plain, str) or not isinstance(hashed, str):
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class TokenPayload:
    """Decoded shape of a successfully verified JWT."""

    sub: str
    type: TokenType
    exp: datetime
    raw: dict[str, Any]


def _create_token(
    subject: UUID | str,
    *,
    token_type: TokenType,
    expires_delta: timedelta,
    settings: Settings | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        # Reserved names take precedence — we never let a caller spoof
        # `type`, `sub`, or `exp`.
        for k, v in extra_claims.items():
            if k in {"sub", "type", "exp", "iat"}:
                continue
            payload[k] = v
    return jwt.encode(
        payload,
        cfg.JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )


def create_access_token(
    subject: UUID | str,
    *,
    settings: Settings | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    cfg = settings or get_settings()
    return _create_token(
        subject,
        token_type=ACCESS_TOKEN_TYPE,
        expires_delta=timedelta(minutes=cfg.ACCESS_TOKEN_EXPIRE_MINUTES),
        settings=cfg,
        extra_claims=extra_claims,
    )


def create_refresh_token(
    subject: UUID | str,
    *,
    settings: Settings | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    cfg = settings or get_settings()
    return _create_token(
        subject,
        token_type=REFRESH_TOKEN_TYPE,
        expires_delta=timedelta(days=cfg.REFRESH_TOKEN_EXPIRE_DAYS),
        settings=cfg,
        extra_claims=extra_claims,
    )


# ---------------------------------------------------------------------
# Connector tokens (separate secret per CONNECTOR_PROTOCOL.md)
# ---------------------------------------------------------------------


CONNECTOR_TOKEN_KIND = "connector"
CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS = 365


@dataclass(frozen=True)
class ConnectorTokenPayload:
    sub: str  # connector id
    company_id: str
    exp: datetime
    raw: dict[str, Any]


def create_connector_token(
    *,
    connector_id: UUID | str,
    company_id: UUID | str,
    expires_days: int = CONNECTOR_TOKEN_DEFAULT_EXPIRE_DAYS,
    settings: Settings | None = None,
) -> str:
    """Create a long-lived connector token (default 1 year).

    Signed with `CONNECTOR_JWT_SECRET` (distinct from the user JWT
    secret) so a user-token compromise can't forge connectors and
    vice versa.
    """
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(connector_id),
        "company_id": str(company_id),
        "kind": CONNECTOR_TOKEN_KIND,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=expires_days)).timestamp()),
    }
    return jwt.encode(
        payload,
        cfg.CONNECTOR_JWT_SECRET.get_secret_value(),
        algorithm=cfg.JWT_ALGORITHM,
    )


def decode_connector_token(
    token: str, *, settings: Settings | None = None
) -> ConnectorTokenPayload:
    cfg = settings or get_settings()
    try:
        decoded = jwt.decode(
            token,
            cfg.CONNECTOR_JWT_SECRET.get_secret_value(),
            algorithms=[cfg.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired(str(exc)) from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid(str(exc)) from exc

    sub = decoded.get("sub")
    company_id = decoded.get("company_id")
    kind = decoded.get("kind")
    exp = decoded.get("exp")
    if not isinstance(sub, str) or not sub:
        raise TokenInvalid("missing sub claim")
    if not isinstance(company_id, str) or not company_id:
        raise TokenInvalid("missing company_id claim")
    if kind != CONNECTOR_TOKEN_KIND:
        raise TokenInvalid("not a connector token")
    if not isinstance(exp, int):
        raise TokenInvalid("missing exp claim")
    return ConnectorTokenPayload(
        sub=sub,
        company_id=company_id,
        exp=datetime.fromtimestamp(exp, tz=UTC),
        raw=decoded,
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType | None = None,
    settings: Settings | None = None,
) -> TokenPayload:
    """Decode and verify a JWT. Raise on any failure mode.

    `expected_type` lets callers reject an access token presented to
    the refresh endpoint (and vice versa).
    """
    cfg = settings or get_settings()
    try:
        decoded = jwt.decode(
            token,
            cfg.JWT_SECRET.get_secret_value(),
            algorithms=[cfg.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired(str(exc)) from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid(str(exc)) from exc

    sub = decoded.get("sub")
    type_ = decoded.get("type")
    exp = decoded.get("exp")
    if not isinstance(sub, str) or not sub:
        raise TokenInvalid("missing sub claim")
    if type_ not in (ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE):
        raise TokenInvalid("missing or invalid type claim")
    if not isinstance(exp, int):
        raise TokenInvalid("missing exp claim")
    if expected_type is not None and type_ != expected_type:
        raise TokenInvalid(
            f"expected {expected_type} token, got {type_}"
        )
    return TokenPayload(
        sub=sub,
        type=type_,  # type: ignore[arg-type]
        exp=datetime.fromtimestamp(exp, tz=UTC),
        raw=decoded,
    )
