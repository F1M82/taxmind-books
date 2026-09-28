"""Tests for the historical voucher import run (Phase B).

Covers window splitting, the anchor guards, the run itself (dry-run vs
persist, idempotent re-run, per-window commit, fail-closed identity, the
connector's ``tally_period_not_covered`` stop) and the two endpoints.

The run opens its own ``SessionLocal`` sessions (like the other background
persists), so fixtures here commit for real; the suite truncates between
tests. The connector is a scripted fake registry.
"""

from __future__ import annotations

import asyncio
from datetime import date
from itertools import pairwise
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.exceptions import (
    OpeningBalanceNotSeeded,
    VoucherImportBeforeAnchor,
)
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import Voucher
from app.services.tally import connector_registry as registry_mod
from app.services.tally import voucher_import_run as vir
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

GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"
ANCHOR = date(2025, 4, 1)


# ---------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------


def test_monthly_windows_single_month_clipped() -> None:
    assert vir.monthly_windows(date(2025, 4, 10), date(2025, 4, 20)) == [
        (date(2025, 4, 10), date(2025, 4, 20))
    ]


def test_monthly_windows_spans_months_and_year_boundary() -> None:
    assert vir.monthly_windows(date(2025, 12, 15), date(2026, 2, 10)) == [
        (date(2025, 12, 15), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 10)),
    ]


def test_monthly_windows_full_financial_year_is_twelve() -> None:
    w = vir.monthly_windows(date(2025, 4, 1), date(2026, 3, 31))
    assert len(w) == 12
    assert w[0][0] == date(2025, 4, 1) and w[-1][1] == date(2026, 3, 31)
    # Contiguous, no gaps or overlaps.
    for (_, prev_end), (nxt_start, _) in pairwise(w):
        assert (nxt_start - prev_end).days == 1


def test_monthly_windows_leap_february() -> None:
    w = vir.monthly_windows(date(2028, 2, 1), date(2028, 2, 29))
    assert w == [(date(2028, 2, 1), date(2028, 2, 29))]


def test_monthly_windows_reversed_range_is_empty() -> None:
    assert vir.monthly_windows(date(2025, 5, 1), date(2025, 4, 1)) == []


def _co(anchor: date | None) -> Any:
    return SimpleNamespace(opening_balance_anchor_date=anchor)


def test_check_import_range_requires_seeded_anchor() -> None:
    with pytest.raises(OpeningBalanceNotSeeded):
        vir.check_import_range(
            _co(None), from_date=date(2025, 4, 1), to_date=date(2025, 4, 30)
        )


def test_check_import_range_refuses_before_anchor() -> None:
    with pytest.raises(VoucherImportBeforeAnchor) as ei:
        vir.check_import_range(
            _co(ANCHOR), from_date=date(2025, 3, 31), to_date=date(2025, 4, 30)
        )
    assert ei.value.details["anchor_date"] == "2025-04-01"


def test_check_import_range_allows_start_on_anchor() -> None:
    vir.check_import_range(
        _co(ANCHOR), from_date=ANCHOR, to_date=date(2026, 3, 31)
    )


def test_check_import_range_refuses_reversed() -> None:
    with pytest.raises(VoucherImportBeforeAnchor):
        vir.check_import_range(
            _co(ANCHOR), from_date=date(2025, 5, 1), to_date=date(2025, 4, 1)
        )


# ---------------------------------------------------------------------
# Fake connector + fixtures
# ---------------------------------------------------------------------


class FakeRegistry:
    """Scripted stand-in for the connector registry."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.active_guid: str | None = GUID
        # window from_date (YYYYMMDD) -> reply (or exception) for export_vouchers
        self.exports: dict[str, Any] = {}
        self.online = True

    def is_online(self, company_id: UUID) -> bool:
        return self.online

    async def send_command(
        self, *, command: str, args: dict[str, Any], **_kw: Any
    ) -> dict[str, Any]:
        self.calls.append((command, args))
        if command == "get_active_tally_company":
            return {
                "status": "success",
                "result": {"tally_company_guid": self.active_guid},
            }
        if command == "export_vouchers":
            reply = self.exports.get(args["from_date"])
            if reply is None:
                reply = _ok([])
            if isinstance(reply, Exception):
                raise reply
            return reply
        raise AssertionError(f"unexpected command {command}")

    def export_calls(self) -> list[dict[str, Any]]:
        return [a for c, a in self.calls if c == "export_vouchers"]


def _ok(vouchers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "success", "result": {"vouchers": vouchers}}


def _row(guid: str, day: str, *, vtype: str = "Receipt", amount: str = "100.00") -> dict[str, Any]:
    return {
        "tally_guid": guid,
        "voucher_type": vtype,
        "date": day,
        "voucher_number": None,
        "narration": None,
        "reference": None,
        "master_id": None,
        "vchkey": None,
        "alter_id": None,
        "is_cancelled": False,
        "is_optional": False,
        "is_deleted": False,
        "entries": [
            {"ledger_name": "Cash", "ledger_guid": None, "amount": amount, "entry_type": "Dr"},
            {"ledger_name": "Sales", "ledger_guid": None, "amount": amount, "entry_type": "Cr"},
        ],
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    reg = FakeRegistry()
    monkeypatch.setattr(registry_mod, "get_registry", lambda: reg)
    vir.clear_runs()
    yield reg
    vir.clear_runs()


def _seeded_company(db: Session):  # type: ignore[no-untyped-def]
    company = make_company(db, tally_master_id=GUID)
    company.opening_balance_anchor_date = ANCHOR
    for name in ("Cash", "Sales"):
        db.add(
            Ledger(
                company_id=company.id,
                name=name,
                name_normalized=name.lower(),
                group_name="Cash-in-Hand" if name == "Cash" else "Sales Accounts",
                tally_master_id=f"g-{name}",
            )
        )
    db.commit()
    db.refresh(company)
    return company


def _run(company, frm: date, to: date, *, dry_run: bool):  # type: ignore[no-untyped-def]
    run = vir.new_run(
        company_id=company.id, from_date=frm, to_date=to, dry_run=dry_run
    )
    asyncio.run(vir.execute_import_run(run, user_id=None))
    return run


def _voucher_count(db: Session, company) -> int:  # type: ignore[no-untyped-def]
    db.expire_all()
    return db.query(Voucher).filter(Voucher.company_id == company.id).count()


# ---------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------


def test_dry_run_counts_but_writes_nothing(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.exports["20250401"] = _ok(
        [_row("guid-1", "2025-04-03"), _row("guid-2", "2025-04-09")]
    )
    run = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=True)
    assert run.state == "completed", run.error
    assert run.windows_done == run.windows_total == 1
    assert run.totals["total"] == 2 and run.totals["insert"] == 2
    assert _voucher_count(db_session, company) == 0


def test_persist_writes_and_rerun_is_idempotent(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.exports["20250401"] = _ok(
        [_row("guid-1", "2025-04-03"), _row("guid-2", "2025-04-09")]
    )
    first = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert first.state == "completed", first.error
    assert first.totals["inserted"] == 2
    assert _voucher_count(db_session, company) == 2

    second = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert second.state == "completed", second.error
    assert second.totals["inserted"] == 0 and second.totals["updated"] == 2
    assert _voucher_count(db_session, company) == 2  # no duplicates


def test_multi_window_calls_and_totals(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.exports["20250401"] = _ok([_row("g-apr", "2025-04-05")])
    fake.exports["20250501"] = _ok([_row("g-may", "2025-05-05")])
    fake.exports["20250601"] = _ok([_row("g-jun", "2025-06-05")])
    run = _run(company, date(2025, 4, 1), date(2025, 6, 15), dry_run=False)
    assert run.state == "completed", run.error
    assert run.windows_total == run.windows_done == 3
    assert [
        (a["from_date"], a["to_date"]) for a in fake.export_calls()
    ] == [
        ("20250401", "20250430"),
        ("20250501", "20250531"),
        ("20250601", "20250615"),
    ]
    assert run.totals["inserted"] == 3
    assert _voucher_count(db_session, company) == 3


def test_failure_stops_run_and_keeps_earlier_windows(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.exports["20250401"] = _ok([_row("g-apr", "2025-04-05")])
    fake.exports["20250501"] = {
        "status": "error",
        "error": {
            "code": "tally_period_not_covered",
            "message": "set the period at the Gateway of Tally main menu",
        },
        "retryable": False,
    }
    fake.exports["20250601"] = _ok([_row("g-jun", "2025-06-05")])
    run = _run(company, date(2025, 4, 1), date(2025, 6, 30), dry_run=False)
    assert run.state == "failed"
    assert run.error["code"] == "tally_period_not_covered"
    assert run.windows_done == 1
    # April was committed; June was never requested.
    assert _voucher_count(db_session, company) == 1
    assert "20250601" not in [a["from_date"] for a in fake.export_calls()]


def test_identity_mismatch_fails_closed_before_any_export(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.active_guid = "some-other-company-guid"
    fake.exports["20250401"] = _ok([_row("g-apr", "2025-04-05")])
    run = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert run.state == "failed"
    assert run.error["code"] == "company_mapping_conflict"
    assert fake.export_calls() == []  # never even asked Tally for vouchers
    assert _voucher_count(db_session, company) == 0


def test_missing_active_guid_fails_closed(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.active_guid = None
    run = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert run.state == "failed"
    assert run.error["code"] == "company_mapping_conflict"
    assert fake.export_calls() == []


def test_transport_failure_is_reported_not_raised(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    fake.exports["20250401"] = TimeoutError("no reply")
    run = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert run.state == "failed"
    assert run.error["code"] == "connector_unreachable"
    assert run.finished_at is not None


def test_unresolvable_ledger_goes_to_manual_review_not_written(
    db_session: Session, fake: FakeRegistry
) -> None:
    company = _seeded_company(db_session)
    bad = _row("g-bad", "2025-04-05")
    bad["entries"][0]["ledger_name"] = "No Such Ledger"
    fake.exports["20250401"] = _ok([bad, _row("g-ok", "2025-04-06")])
    run = _run(company, date(2025, 4, 1), date(2025, 4, 30), dry_run=False)
    assert run.state == "completed", run.error
    assert run.totals["inserted"] == 1 and run.totals["manual_review"] == 1
    assert _voucher_count(db_session, company) == 1


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------


class _FakeWS:
    async def send_text(self, data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


def _headers(user, company, idem: str | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    h = {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }
    if idem:
        h["Idempotency-Key"] = idem
    return h


@pytest.fixture
def endpoint_env(  # type: ignore[no-untyped-def]
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """Owner + seeded company + a registered (fake-WS) connector, with the
    background run stubbed out so the 202 tests don't drive a real run."""
    reg = get_registry()
    reg._by_company.clear()
    vir.clear_runs()
    started: list[Any] = []

    async def _noop(run, *, user_id):  # type: ignore[no-untyped-def]
        started.append(run)

    monkeypatch.setattr("app.api.v1.connector.execute_import_run", _noop)
    user = make_user(db_session)
    company = _seeded_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    conn = ConnectorConnection(
        company_id=company.id, connector_id=uuid4(), ws=_FakeWS()  # type: ignore[arg-type]
    )
    asyncio.run(reg.register(conn))
    yield SimpleNamespace(user=user, company=company, started=started, db=db_session)
    reg._by_company.clear()
    vir.clear_runs()


def _post(client: TestClient, env, body: dict[str, Any], *, idem: str | None = "auto"):  # type: ignore[no-untyped-def]
    key = str(uuid4()) if idem == "auto" else idem
    return client.post(
        f"/api/v1/connector/voucher-import/{env.company.id}",
        json=body,
        headers=_headers(env.user, env.company, key),
    )


def test_trigger_defaults_to_dry_run_and_returns_202(
    client: TestClient, endpoint_env
) -> None:
    r = _post(client, endpoint_env, {"from_date": "2025-04-01", "to_date": "2025-06-30"})
    assert r.status_code == 202, r.json()
    body = r.json()
    assert body["status"] == "import_triggered"
    assert body["dry_run"] is True
    assert body["windows_total"] == 3
    UUID(body["task_id"])


def test_trigger_explicit_persist(client: TestClient, endpoint_env) -> None:
    r = _post(
        client,
        endpoint_env,
        {"from_date": "2025-04-01", "to_date": "2025-04-30", "dry_run": False},
    )
    assert r.status_code == 202
    assert r.json()["dry_run"] is False


def test_trigger_409_when_opening_balances_not_seeded(
    client: TestClient, endpoint_env
) -> None:
    env = endpoint_env
    env.company.opening_balance_anchor_date = None
    env.db.commit()
    r = _post(client, env, {"from_date": "2025-04-01", "to_date": "2025-04-30"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "opening_balance_not_seeded"


def test_trigger_422_when_before_anchor(client: TestClient, endpoint_env) -> None:
    r = _post(client, endpoint_env, {"from_date": "2025-03-01", "to_date": "2025-04-30"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "voucher_import_before_anchor"


def test_trigger_400_without_idempotency_key(
    client: TestClient, endpoint_env
) -> None:
    r = _post(
        client,
        endpoint_env,
        {"from_date": "2025-04-01", "to_date": "2025-04-30"},
        idem=None,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_required"


def test_trigger_replay_returns_same_task(client: TestClient, endpoint_env) -> None:
    key = str(uuid4())
    body = {"from_date": "2025-04-01", "to_date": "2025-04-30"}
    r1 = _post(client, endpoint_env, body, idem=key)
    r2 = _post(client, endpoint_env, body, idem=key)
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["task_id"] == r2.json()["task_id"]
    assert r2.headers.get("Idempotent-Replay") == "true"


def test_trigger_503_when_connector_offline(
    client: TestClient, endpoint_env
) -> None:
    get_registry()._by_company.clear()
    r = _post(client, endpoint_env, {"from_date": "2025-04-01", "to_date": "2025-04-30"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "connector_offline"


def test_trigger_requires_owner(
    client: TestClient, endpoint_env, db_session: Session
) -> None:
    env = endpoint_env
    accountant = make_user(db_session)
    make_membership(db_session, accountant, env.company, role=CompanyRole.accountant)
    r = client.post(
        f"/api/v1/connector/voucher-import/{env.company.id}",
        json={"from_date": "2025-04-01", "to_date": "2025-04-30"},
        headers=_headers(accountant, env.company, str(uuid4())),
    )
    assert r.status_code == 403


def test_status_reports_progress_and_error(
    client: TestClient, endpoint_env
) -> None:
    env = endpoint_env
    run = vir.new_run(
        company_id=env.company.id,
        from_date=date(2025, 4, 1),
        to_date=date(2025, 5, 31),
        dry_run=True,
    )
    run.windows_done = 1
    run.state = "failed"
    run.error = {"code": "tally_period_not_covered", "message": "set the period"}
    run.add_counts({"total": 4, "insert": 4})
    r = client.get(
        f"/api/v1/connector/voucher-import/{run.task_id}",
        headers=_headers(env.user, env.company),
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["state"] == "failed" and body["windows_done"] == 1
    assert body["windows_total"] == 2
    assert body["totals"] == {"total": 4, "insert": 4}
    assert body["error"]["code"] == "tally_period_not_covered"


def test_status_404_for_unknown_run(client: TestClient, endpoint_env) -> None:
    r = client.get(
        f"/api/v1/connector/voucher-import/{uuid4()}",
        headers=_headers(endpoint_env.user, endpoint_env.company),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "voucher_import_run_not_found"


def test_status_hides_other_companies_runs(
    client: TestClient, endpoint_env, db_session: Session
) -> None:
    env = endpoint_env
    other = make_company(db_session)
    run = vir.new_run(
        company_id=other.id,
        from_date=date(2025, 4, 1),
        to_date=date(2025, 4, 30),
        dry_run=True,
    )
    r = client.get(
        f"/api/v1/connector/voucher-import/{run.task_id}",
        headers=_headers(env.user, env.company),
    )
    assert r.status_code == 404
