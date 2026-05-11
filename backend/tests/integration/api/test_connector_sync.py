"""Integration tests for POST /api/v1/connector/sync/{company_id} (P0.27)."""

from __future__ import annotations

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


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


def _h(user, company, *, idem: str | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    h = {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


def _setup(db_session: Session, *, register_connector: bool = True):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    if register_connector:
        import asyncio

        conn = ConnectorConnection(
            company_id=company.id, connector_id=uuid4(), ws=_FakeWS()  # type: ignore[arg-type]
        )
        asyncio.run(get_registry().register(conn))
    return user, company


# ---------------- 202 ----------------


def test_sync_returns_202_when_connector_online(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        f"/api/v1/connector/sync/{company.id}",
        headers=_h(user, company, idem=str(uuid4())),
    )
    assert r.status_code == 202, r.json()
    body = r.json()
    assert body["status"] == "sync_triggered"
    UUID(body["task_id"])
    assert body["estimated_duration_seconds"] > 0


# ---------------- 503 connector offline ----------------


def test_sync_503_when_connector_offline(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session, register_connector=False)
    r = client.post(
        f"/api/v1/connector/sync/{company.id}",
        headers=_h(user, company, idem=str(uuid4())),
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "connector_offline"


# ---------------- 400 idempotency_key_required ----------------


def test_sync_400_without_idempotency_key(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    r = client.post(
        f"/api/v1/connector/sync/{company.id}", headers=_h(user, company)
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_required"


# ---------------- idempotency replay ----------------


def test_sync_replay_returns_same_task_id(
    client: TestClient, db_session: Session
) -> None:
    user, company = _setup(db_session)
    key = str(uuid4())
    r1 = client.post(
        f"/api/v1/connector/sync/{company.id}",
        headers=_h(user, company, idem=key),
    )
    assert r1.status_code == 202
    task_id_1 = r1.json()["task_id"]

    r2 = client.post(
        f"/api/v1/connector/sync/{company.id}",
        headers=_h(user, company, idem=key),
    )
    assert r2.status_code == 202
    assert r2.json()["task_id"] == task_id_1
    assert r2.headers.get("Idempotent-Replay") == "true"


# ---------------- path / header mismatch ----------------


def test_sync_path_mismatch_with_header(
    client: TestClient, db_session: Session
) -> None:
    """Path company_id must equal X-Company-ID."""
    user, company = _setup(db_session)
    other_id = uuid4()
    r = client.post(
        f"/api/v1/connector/sync/{other_id}",
        headers=_h(user, company, idem=str(uuid4())),
    )
    # Not 404 (we can't reveal whether `other_id` is a real company);
    # 503 from our path-mismatch guard.
    assert r.status_code == 503


# ---------------- tenant isolation ----------------


def test_sync_non_member_404(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    # No membership.
    r = client.post(
        f"/api/v1/connector/sync/{company.id}",
        headers=_h(user, company, idem=str(uuid4())),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"
