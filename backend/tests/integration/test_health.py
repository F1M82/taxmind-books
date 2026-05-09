"""Integration test: the FastAPI app comes up and `/health` returns 200."""

from __future__ import annotations

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
