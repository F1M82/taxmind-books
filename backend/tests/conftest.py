"""Shared pytest fixtures for backend tests.

Phase 0 starts narrow: deterministic env + a settings factory. DB and
HTTP client fixtures are introduced as later tasks need them.

Note on import ordering: env vars are seeded at *module import* (not in
a fixture) because `app.main` calls `create_app()` at module top level,
and that runs while pytest is collecting tests — before any
function-scoped fixture has had a chance to fire.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://taxmind:taxmind@localhost:5432/taxmind_books_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "JWT_SECRET": "test-jwt-secret-do-not-use-in-prod",
    "SECRET_KEY": "test-secret-key-do-not-use-in-prod",
    "CONNECTOR_JWT_SECRET": "test-connector-secret-do-not-use-in-prod",
    "APP_ENV": "test",
    # Suppress the Celery dispatch in `enqueue_voucher_post` so we don't
    # block on a non-existent Redis broker during tests. Tests that
    # actually exercise the dispatcher do so by calling
    # `dispatch_voucher_to_tally` directly.
    "TAXMIND_SKIP_TALLY_DISPATCH": "1",
}

# Seed env at conftest import time so test-module imports of `app.main`
# succeed during pytest collection. setdefault means any developer-set
# values still win.
for _key, _value in _REQUIRED_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _isolate_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[None, None, None]:
    """Reassert env values per-test and chdir to a clean tmp dir.

    `chdir` keeps a developer's local `.env` from leaking in. The
    re-asserted env values protect tests that follow ones that called
    `monkeypatch.delenv(...)`.
    """
    monkeypatch.chdir(tmp_path)
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    yield
