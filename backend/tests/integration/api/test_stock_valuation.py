"""Per-financial-year stock valuation: recording, guards, and its effect on the
profit & loss, balance sheet and dashboard (docs/PHASE_3_CLOSING_STOCK_DESIGN.md).

Miniature of the Vighnaharta situation, numbers chosen to be checkable by hand:

  Opening TB (anchor 2025-04-01):  Stock-in-Hand ledger Dr 700, Bank Dr 300,
                                   Capital Cr 1000.
  FY 2025-26:  sale 500 (Bank Dr / Sales Cr), purchase 200 (Purchase Dr / Bank Cr)
               -> income 500, expense 200, ledger-only profit 300.
  FY 2026-27:  sale 100.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import BalanceType, Ledger
from app.models.stock_valuation import StockValuation
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.reporting.balance_sheet import compute_balance_sheet
from app.services.reporting.stock import (
    balance_sheet_stock,
    stock_effect_for_window,
)
from app.services.tally import connector_registry as registry_mod
from app.services.tally.connector_registry import CommandTimeout
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
FY1 = ("2025-04-01", "2026-03-31")


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _ledger(db: Session, company, name: str, group: str, kind: BalanceType, opening: str = "0"):  # type: ignore[no-untyped-def]
    row = Ledger(
        company_id=company.id,
        name=name,
        name_normalized=name.lower(),
        group_name=group,
        balance_type=kind,
        opening_balance=Decimal(opening),
    )
    db.add(row)
    db.commit()
    return row


def _voucher(db: Session, company, on: date, dr, cr, amount: str, vtype=VoucherType.Journal) -> None:  # type: ignore[no-untyped-def]
    v = Voucher(
        company_id=company.id,
        voucher_type=vtype,
        date=on,
        total_amount=Decimal(amount),
        status=VoucherStatus.posted,
        source="manual",
        is_auto_posted=False,
        gst_applicable=False,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company.id, voucher_id=v.id, ledger_id=dr.id,
                amount=Decimal(amount), entry_type=EntryType.Dr, line_number=1,
            ),
            LedgerEntry(
                company_id=company.id, voucher_id=v.id, ledger_id=cr.id,
                amount=Decimal(amount), entry_type=EntryType.Cr, line_number=2,
            ),
        ]
    )
    db.commit()


def _world(db: Session, *, role: CompanyRole = CompanyRole.owner):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db, tally_master_id=GUID)
    company.opening_balance_anchor_date = ANCHOR
    db.commit()
    make_membership(db, user, company, role=role)
    stock = _ledger(db, company, "Opening Stock", "Stock-in-Hand", BalanceType.Dr, "700.00")
    bank = _ledger(db, company, "Bank", "Bank Accounts", BalanceType.Dr, "300.00")
    _ledger(db, company, "Capital", "Capital Account", BalanceType.Cr, "1000.00")
    sales = _ledger(db, company, "Sales", "Sales Accounts", BalanceType.Cr)
    purchase = _ledger(db, company, "Purchases", "Purchase Accounts", BalanceType.Dr)
    _voucher(db, company, date(2025, 6, 10), bank, sales, "500.00", VoucherType.Sales)
    _voucher(db, company, date(2025, 7, 10), purchase, bank, "200.00", VoucherType.Purchase)
    return SimpleNamespace(user=user, company=company, stock=stock, bank=bank, sales=sales)


def _put(client: TestClient, w, period_from: str, opening: str, closing: str):  # type: ignore[no-untyped-def]
    return client.put(
        f"/api/v1/stock-valuations/{period_from}",
        json={"opening_value": opening, "closing_value": closing},
        headers=_h(w.user, w.company),
    )


def _pnl(client: TestClient, w, frm: str, to: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = client.get(
        f"/api/v1/reports/profit-loss?from_date={frm}&to_date={to}",
        headers=_h(w.user, w.company),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bs(client: TestClient, w, as_of: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = client.get(
        f"/api/v1/reports/balance-sheet?as_of_date={as_of}",
        headers=_h(w.user, w.company),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _lines(section: dict[str, Any]) -> dict[str, str]:
    return {
        line["ledger_name"]: line["amount"]
        for g in section["groups"]
        for line in g["ledgers"]
    }


# ---------------------------------------------------------------------
# Without a valuation nothing changes (regression guard)
# ---------------------------------------------------------------------


def test_without_valuation_reports_are_unchanged(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    pnl = _pnl(client, w, *FY1)
    assert pnl["stock"] is None
    assert pnl["net"] == {"value": "300.00", "type": "profit"}
    bs = _bs(client, w, "2026-03-31")
    assert bs["stock_applied"] is False
    assert _lines(bs["assets"])["Opening Stock"] == "700.00"
    assert bs["equation"]["in_balance"] is True


# ---------------------------------------------------------------------
# Applying a valuation
# ---------------------------------------------------------------------


def test_pnl_folds_in_opening_and_closing_stock(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    assert _put(client, w, "2025-04-01", "700.00", "400.00").status_code == 200
    pnl = _pnl(client, w, *FY1)
    assert pnl["stock"]["opening_value"] == "700.00"
    assert pnl["stock"]["closing_value"] == "400.00"
    assert pnl["stock"]["source"] == "manual"
    # Income/expense totals are untouched; only net moves: 500-200+400-700.
    assert pnl["income"]["total"] == "500.00"
    assert pnl["expense"]["total"] == "200.00"
    assert pnl["net"] == {"value": "0.00", "type": "profit"}


def test_pnl_for_a_part_of_the_year_ignores_stock(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    _put(client, w, "2025-04-01", "700.00", "400.00")
    pnl = _pnl(client, w, "2025-06-01", "2026-03-31")  # does not start on 1 April
    assert pnl["stock"] is None
    assert pnl["net"]["value"] == "300.00"
    pnl = _pnl(client, w, "2025-04-01", "2025-12-31")  # ends mid-year (past FY)
    assert pnl["stock"] is None


def test_balance_sheet_replaces_stock_ledger_and_still_balances(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    _put(client, w, "2025-04-01", "700.00", "400.00")
    bs = _bs(client, w, "2026-03-31")
    assert bs["stock_applied"] is True
    lines = _lines(bs["assets"])
    assert "Opening Stock" not in lines
    assert lines["Closing Stock"] == "400.00"
    # Bank 300+500-200 = 600, plus stock 400.
    assert bs["equation"]["assets"] == "1000.00"
    assert bs["equation"]["liabilities_plus_equity"] == "1000.00"
    assert bs["equation"]["in_balance"] is True
    assert bs["current_period_profit_loss"] == {"value": "0.00", "type": "profit"}


def test_net_credit_closing_stock_is_shown_faithfully_not_clamped(
    client: TestClient, db_session: Session
) -> None:
    """Tally reports negative stock as a net credit; mirror it."""
    w = _world(db_session)
    assert _put(client, w, "2025-04-01", "700.00", "-50.00").status_code == 200
    pnl = _pnl(client, w, *FY1)
    assert pnl["stock"]["closing_value"] == "-50.00"
    assert pnl["net"] == {"value": "450.00", "type": "loss"}  # 300 + (-50 - 700)
    bs = _bs(client, w, "2026-03-31")
    assert bs["stock_applied"] is True
    assert _lines(bs["assets"])["Closing Stock"] == "-50.00"
    assert bs["equation"]["in_balance"] is True


def test_dashboard_net_profit_uses_the_stock_adjusted_result(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    _put(client, w, "2025-04-01", "700.00", "400.00")
    r = client.get(
        "/api/v1/dashboard/financials?from=2025-04-01&to=2026-03-31",
        headers=_h(w.user, w.company),
    )
    assert r.status_code == 200, r.text
    assert r.json()["net_profit"] == {"value": "0.00", "type": "profit"}


def test_stale_valuation_falls_back_instead_of_breaking_the_sheet(
    client: TestClient, db_session: Session
) -> None:
    """A valuation whose opening no longer matches the ledger (bypassing the
    service check) must not turn a good balance sheet into a 500."""
    w = _world(db_session)
    db_session.add(
        StockValuation(
            company_id=w.company.id,
            period_from=date(2025, 4, 1),
            period_to=date(2026, 3, 31),
            opening_value=Decimal("650.00"),  # ledger says 700
            closing_value=Decimal("400.00"),
            source="manual",
            captured_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    bs = _bs(client, w, "2026-03-31")
    assert bs["stock_applied"] is False
    assert bs["equation"]["in_balance"] is True
    assert _lines(bs["assets"])["Opening Stock"] == "700.00"


# ---------------------------------------------------------------------
# Guards on recording
# ---------------------------------------------------------------------


def test_first_year_opening_must_match_the_stock_ledger(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    r = _put(client, w, "2025-04-01", "600.00", "400.00")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "stock_opening_mismatch"
    assert err["details"]["stock_in_hand_ledger_balance"] == "700.00"
    assert db_session.query(StockValuation).count() == 0


def test_later_year_opening_is_not_tied_to_the_ledger(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    assert _put(client, w, "2026-04-01", "400.00", "350.00").status_code == 200


def test_period_must_be_exactly_one_financial_year(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    r = _put(client, w, "2025-05-01", "700.00", "400.00")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "stock_period_not_single_fy"


def test_rerecording_a_year_replaces_it_and_is_audited(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    _put(client, w, "2025-04-01", "700.00", "400.00")
    assert _put(client, w, "2025-04-01", "700.00", "450.00").status_code == 200
    db_session.expire_all()
    rows = db_session.query(StockValuation).all()
    assert len(rows) == 1 and rows[0].closing_value == Decimal("450.00")
    audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "stock_valuation.recorded")
        .all()
    )
    assert len(audits) == 2


def test_list_is_readable_by_a_viewer_but_writing_is_not(
    client: TestClient, db_session: Session
) -> None:
    w = _world(db_session)
    _put(client, w, "2025-04-01", "700.00", "400.00")
    viewer = make_user(db_session)
    make_membership(db_session, viewer, w.company, role=CompanyRole.viewer)
    r = client.get("/api/v1/stock-valuations", headers=_h(viewer, w.company))
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["label"] for i in items] == ["FY 2025-26"]
    assert items[0]["opening_value"] == "700.00"
    denied = client.put(
        "/api/v1/stock-valuations/2025-04-01",
        json={"opening_value": "700.00", "closing_value": "1.00"},
        headers=_h(viewer, w.company),
    )
    assert denied.status_code == 403


# ---------------------------------------------------------------------
# Multi-year and "today" semantics (pinned clock, service level)
# ---------------------------------------------------------------------

TODAY = date(2026, 9, 28)  # inside FY 2026-27


def _put_rows(db: Session, company, rows: list[tuple[int, str, str]]) -> None:  # type: ignore[no-untyped-def]
    for year, opening, closing in rows:
        db.add(
            StockValuation(
                company_id=company.id,
                period_from=date(year, 4, 1),
                period_to=date(year + 1, 3, 31),
                opening_value=Decimal(opening),
                closing_value=Decimal(closing),
                source="tally",
                captured_at=datetime.now(UTC),
            )
        )
    db.commit()


def test_current_year_applies_only_from_today_onwards(db_session: Session) -> None:
    w = _world(db_session)
    _put_rows(db_session, w.company, [(2026, "400.00", "350.00")])
    kw = {"company_id": w.company.id, "from_date": date(2026, 4, 1), "today": TODAY}
    assert stock_effect_for_window(db_session, to_date=TODAY, **kw) is not None
    assert (
        stock_effect_for_window(db_session, to_date=date(2027, 3, 31), **kw)
        is not None
    )
    # Mid-year in the past: closing stock at that date is unknown.
    assert (
        stock_effect_for_window(db_session, to_date=date(2026, 7, 1), **kw) is None
    )


def test_window_spanning_years_needs_only_first_opening_and_last_closing(
    db_session: Session,
) -> None:
    w = _world(db_session)
    _put_rows(db_session, w.company, [(2025, "700.00", "400.00"), (2026, "400.00", "350.00")])
    e = stock_effect_for_window(
        db_session,
        company_id=w.company.id,
        from_date=date(2025, 4, 1),
        to_date=TODAY,
        today=TODAY,
    )
    assert e is not None
    assert (e.opening_value, e.closing_value) == (Decimal("700.00"), Decimal("350.00"))
    assert e.net == Decimal("-350.00")


def test_balance_sheet_in_a_later_year_needs_every_year_it_depends_on(
    db_session: Session,
) -> None:
    w = _world(db_session)
    _voucher(db_session, w.company, date(2026, 5, 1), w.bank, w.sales, "100.00", VoucherType.Sales)
    # Only the current year recorded: the prior year's stock effect is unknown,
    # so stock is not applied at all (all-or-nothing).
    _put_rows(db_session, w.company, [(2026, "400.00", "350.00")])
    bs = compute_balance_sheet(
        db_session,
        company_id=w.company.id,
        as_of_date=TODAY,
        anchor_date=ANCHOR,
        today=TODAY,
    )
    assert bs.stock_applied is False
    assert bs.in_balance is True


def test_balance_sheet_in_a_later_year_with_both_years_balances(
    db_session: Session,
) -> None:
    w = _world(db_session)
    _voucher(db_session, w.company, date(2026, 5, 1), w.bank, w.sales, "100.00", VoucherType.Sales)
    _put_rows(db_session, w.company, [(2025, "700.00", "400.00"), (2026, "400.00", "350.00")])
    bs = compute_balance_sheet(
        db_session,
        company_id=w.company.id,
        as_of_date=TODAY,
        anchor_date=ANCHOR,
        today=TODAY,
    )
    assert bs.stock_applied is True
    assert bs.in_balance is True
    # Bank 700 + stock 350 = 1050; capital 1000 + prior 0 + current 50.
    assert bs.assets.total == Decimal("1050.00")
    assert (bs.prior_periods_pnl_value, bs.prior_periods_pnl_type) == (Decimal("0.00"), "profit")
    assert (bs.current_period_pnl_value, bs.current_period_pnl_type) == (Decimal("50.00"), "profit")


def test_balance_sheet_stock_none_without_an_anchor(db_session: Session) -> None:
    w = _world(db_session)
    _put_rows(db_session, w.company, [(2025, "700.00", "400.00")])
    assert (
        balance_sheet_stock(
            db_session,
            company_id=w.company.id,
            as_of_date=date(2026, 3, 31),
            anchor_date=None,
            today=TODAY,
        )
        is None
    )


# ---------------------------------------------------------------------
# Pull from Tally via the connector
# ---------------------------------------------------------------------


class FakeRegistry:
    def __init__(self) -> None:
        self.online = True
        self.reply: Any = None

    def is_online(self, company_id: Any) -> bool:
        return self.online

    async def send_command(self, **_kw: Any) -> dict[str, Any]:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _payload(**over: Any) -> dict[str, Any]:
    body = {
        "company": {"name": "ACME", "guid": GUID},
        "period_from": "2025-04-01",
        "period_to": "2026-03-31",
        "opening_value": "700.00",
        "closing_value": "-139.82",
        "item_count": 210,
        "items_with_value": 39,
        "negative_stock_items": 18,
    }
    body.update(over)
    return {"status": "success", "result": body}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    reg = FakeRegistry()
    monkeypatch.setattr(registry_mod, "get_registry", lambda: reg)
    return reg


def _pull(client: TestClient, w):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/connector/stock-valuation/{w.company.id}",
        headers=_h(w.user, w.company),
    )


def test_pull_records_the_year_with_data_quality_counts(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = _payload()
    r = _pull(client, w)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "FY 2025-26"
    assert body["source"] == "tally"
    assert body["closing_value"] == "-139.82"
    assert body["negative_stock_items"] == 18 and body["item_count"] == 210
    # And the reports pick it up.
    assert _pnl(client, w, *FY1)["stock"]["closing_value"] == "-139.82"


def test_pull_of_a_multi_year_period_is_refused_with_instructions(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = _payload(period_from="2024-04-01", period_to="2027-03-31")
    r = _pull(client, w)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "stock_period_not_single_fy"
    assert "Gateway of Tally" in r.json()["error"]["message"]
    assert db_session.query(StockValuation).count() == 0


def test_pull_fails_closed_on_the_wrong_tally_company(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = _payload(company={"name": "OTHER", "guid": "some-other-guid"})
    r = _pull(client, w)
    assert r.status_code == 409
    assert db_session.query(StockValuation).count() == 0


def test_pull_surfaces_a_connector_error(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = {
        "status": "error",
        "error": {"code": "TallyUnreachable", "message": "Tally is not running"},
    }
    r = _pull(client, w)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "stock_valuation_pull_failed"
    assert err["details"]["connector_code"] == "TallyUnreachable"


def test_pull_timeout_and_offline(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = CommandTimeout("no reply")
    assert _pull(client, w).status_code == 502
    fake.online = False
    r = _pull(client, w)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "connector_offline"


def test_pull_first_year_opening_mismatch_is_refused(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    fake.reply = _payload(opening_value="733801.87")  # ledger holds 700.00
    r = _pull(client, w)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "stock_opening_mismatch"


def test_pull_requires_owner_or_admin(
    client: TestClient, db_session: Session, fake: FakeRegistry
) -> None:
    w = _world(db_session)
    acct = make_user(db_session)
    make_membership(db_session, acct, w.company, role=CompanyRole.accountant)
    fake.reply = _payload()
    r = client.post(
        f"/api/v1/connector/stock-valuation/{w.company.id}",
        headers=_h(acct, w.company),
    )
    assert r.status_code == 403
