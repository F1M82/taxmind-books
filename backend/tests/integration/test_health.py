"""Integration test: the FastAPI app comes up and `/health` returns 200."""

from __future__ import annotations

import pytest
from app.api.v1 import health as health_module
from app.main import create_app
from fastapi.testclient import TestClient


def test_root_returns_running() -> None:
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "running"}


def test_health_returns_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"


def test_ready_reports_ok_when_dependencies_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_module, "_check_database", lambda: "ok")
    monkeypatch.setattr(health_module, "_check_redis", lambda: "ok")
    resp = TestClient(create_app()).get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "database": "ok", "redis": "ok"}


@pytest.mark.parametrize(
    ("db_status", "redis_status"),
    [("unavailable", "ok"), ("ok", "unavailable"), ("unavailable", "unavailable")],
)
def test_ready_returns_503_when_a_dependency_is_down(
    monkeypatch: pytest.MonkeyPatch, db_status: str, redis_status: str
) -> None:
    monkeypatch.setattr(health_module, "_check_database", lambda: db_status)
    monkeypatch.setattr(health_module, "_check_redis", lambda: redis_status)
    resp = TestClient(create_app()).get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "not_ready",
        "database": db_status,
        "redis": redis_status,
    }


def test_ready_needs_no_auth_or_company_header() -> None:
    """Whatever the dependency state, the route is reachable anonymously
    (never 401/403/422) -- it's a probe, not a tenant route."""
    resp = TestClient(create_app()).get("/api/v1/health/ready")
    assert resp.status_code in (200, 503)
    assert set(resp.json()) == {"status", "database", "redis"}
