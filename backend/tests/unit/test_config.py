"""Unit tests for `app.config.Settings`."""

from __future__ import annotations

import pytest
from app.config import Settings
from pydantic import ValidationError


def test_settings_load_with_required_env() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert s.JWT_SECRET.get_secret_value() == "test-jwt-secret-do-not-use-in-prod"
    assert s.APP_ENV == "test"
    assert s.JWT_ALGORITHM == "HS256"
    assert s.ACCESS_TOKEN_EXPIRE_MINUTES == 30


def test_settings_missing_jwt_secret_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings()  # type: ignore[call-arg]

    msg = str(excinfo.value)
    assert "JWT_SECRET" in msg


def test_settings_missing_database_url_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings()  # type: ignore[call-arg]

    assert "DATABASE_URL" in str(excinfo.value)


def test_cors_origins_includes_web_and_mobile() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert s.WEB_URL in s.cors_origins
    assert s.MOBILE_URL in s.cors_origins


def test_optional_secrets_default_to_none() -> None:
    s = Settings()  # type: ignore[call-arg]
    assert s.ANTHROPIC_API_KEY is None
    assert s.OPENAI_API_KEY is None
    assert s.S3_BUCKET is None
