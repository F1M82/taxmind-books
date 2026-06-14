"""Integration tests for GET /api/v1/connector/status (P0.25)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.models.company import CompanyRole
from app.services.tally.connector_registry import (
    ConnectorConnection,
    get_registry,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reg = get_registry()
    reg._by_company.clear()
    yield
    reg._by_company.clear()


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


class _FakeWS:
    async def send_text(self, data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


# ---------------- disconnected ----------------


def test_status_disconnected_when_no_connector(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.viewer)
    r = client.get("/api/v1/connector/status", headers=_h(user, company))
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["company_id"] == str(company.id)
    assert body["tally_running"] is None


# ---------------- connected ----------------


def test_status_connected_snapshot(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.viewer)

    # Plant a live connector in the registry directly.
    conn = ConnectorConnection(
        company_id=company.id,
        connector_id=uuid4(),
        ws=_FakeWS(),  # type: ignore[arg-type]
    )
    conn.tally_running = True
    conn.tally_version = "3.0"
    conn.connector_version = "1.0.0"
    conn.connector_build_sha = "abc1234"
    conn.connector_built_at = "2026-06-14T00:00:00+00:00"
    conn.queued_outbound_count = 0
    conn.last_heartbeat_at = datetime.now(UTC)
    import asyncio

    asyncio.run(get_registry().register(conn))

    r = client.get("/api/v1/connector/status", headers=_h(user, company))
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["tally_running"] is True
    assert body["tally_version"] == "3.0"
    assert body["connector_version"] == "1.0.0"
    assert body["connector_build_sha"] == "abc1234"
    assert body["connector_built_at"] == "2026-06-14T00:00:00+00:00"
    assert body["queued_outbound_count"] == 0
    UUID(body["company_id"])


# ---------------- tenant boundaries ----------------


def test_status_requires_x_company_id(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get(
        "/api/v1/connector/status",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
    )
    assert r.status_code == 422  # Header(...) missing


def test_status_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/connector/status")
    assert r.status_code == 401


def test_status_404_for_non_member(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    # No membership.
    r = client.get("/api/v1/connector/status", headers=_h(user, company))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"


def test_status_does_not_leak_other_companys_connector(
    client: TestClient, db_session: Session
) -> None:
    """User in A; B has a live connector. The status for A reports
    disconnected — B's connector is not visible."""
    user = make_user(db_session)
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, user, a, role=CompanyRole.viewer)

    import asyncio

    b_conn = ConnectorConnection(
        company_id=b.id, connector_id=uuid4(), ws=_FakeWS()  # type: ignore[arg-type]
    )
    asyncio.run(get_registry().register(b_conn))

    r = client.get("/api/v1/connector/status", headers=_h(user, a))
    assert r.status_code == 200
    assert r.json()["connected"] is False
