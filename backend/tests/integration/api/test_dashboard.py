"""Integration tests for GET /api/v1/dashboard/home (P0.40)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from app.models.company import CompanyRole
from app.models.ledger import BalanceType, Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.tally import connector_registry as registry_mod
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


# ---------------------------------------------------------------------
# Fake registry helpers (avoid touching the real WS infra in tests)
# ---------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self) -> None:
        self._snapshots: dict[UUID, dict[str, Any] | None] = {}

    def status_for(self, company_id: UUID) -> dict[str, Any] | None:
        return self._snapshots.get(company_id)

    def set_status(self, company_id: UUID, snap: dict[str, Any] | None) -> None:
        self._snapshots[company_id] = snap

    async def send_command(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("send_command not expected during dashboard tests")


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> _FakeRegistry:
    fake = _FakeRegistry()
    monkeypatch.setattr(registry_mod, "get_registry", lambda: fake)
    return fake


# ---------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------


def _voucher(  # type: ignore[no-untyped-def]
    db: Session,
    company_id: UUID,
    *,
    voucher_type: VoucherType,
    on_date: date,
    dr_ledger: Ledger,
    cr_ledger: Ledger,
    amount: Decimal,
    status_: VoucherStatus = VoucherStatus.posted,
    is_optional: bool = False,
    gst_applicable: bool = False,
    cgst: Decimal = Decimal("0"),
    sgst: Decimal = Decimal("0"),
    igst: Decimal = Decimal("0"),
) -> Voucher:
    v = Voucher(
        company_id=company_id,
        voucher_type=voucher_type,
        date=on_date,
        total_amount=amount,
        status=status_,
        source="manual",
        is_auto_posted=False,
        gst_applicable=gst_applicable,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        is_optional_in_tally=is_optional,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company_id,
                voucher_id=v.id,
                ledger_id=dr_ledger.id,
                amount=amount,
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company_id,
                voucher_id=v.id,
                ledger_id=cr_ledger.id,
                amount=amount,
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db.commit()
    return v


def _seed(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db, name="Acme Traders")
    make_membership(db, user, company, role=CompanyRole.viewer)
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
    )
    cust = Ledger(
        company_id=company.id,
        name="Acme",
        name_normalized="acme",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    supp = Ledger(
        company_id=company.id,
        name="Supp",
        name_normalized="supp",
        group_name="Sundry Creditors",
        balance_type=BalanceType.Cr,
    )
    sales = Ledger(
        company_id=company.id,
        name="Sales",
        name_normalized="sales",
        group_name="Sales Accounts",
        balance_type=BalanceType.Cr,
    )
    purchase = Ledger(
        company_id=company.id,
        name="Purchase",
        name_normalized="purchase",
        group_name="Purchase Accounts",
        balance_type=BalanceType.Dr,
    )
    db.add_all([bank, cust, supp, sales, purchase])
    db.commit()
    return user, company, bank, cust, supp, sales, purchase


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_dashboard_offline_connector_emits_alert(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, *_ = _seed(db_session)
    fake_registry.set_status(company.id, None)  # offline

    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_name"] == "Acme Traders"
    assert body["connector"]["connected"] is False
    kinds = [a["kind"] for a in body["alerts"]]
    assert "connector_offline" in kinds


def test_dashboard_connected_with_recent_heartbeat_has_no_offline_alert(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, *_ = _seed(db_session)
    fake_registry.set_status(
        company.id,
        {
            "company_id": str(company.id),
            "connected": True,
            "last_seen_at": datetime.now(UTC).isoformat(),
            "tally_running": True,
            "tally_version": "TallyPrime 4.0",
            "connector_version": "0.1.0",
            "queued_outbound_count": 0,
        },
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert body["connector"]["connected"] is True
    assert body["connector"]["tally_running"] is True
    assert body["connector"]["last_seen_seconds_ago"] is not None
    assert body["connector"]["last_seen_seconds_ago"] < 5
    assert all(a["kind"] != "connector_offline" for a in body["alerts"])


def test_dashboard_cash_in_out_aggregates_bank_movement(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, bank, cust, supp, sales, purchase = _seed(db_session)
    # Must match the dashboard service's UTC "today" — using local
    # `date.today()` makes the test flaky across the IST/UTC midnight
    # boundary (~18:30 UTC), which is what bit the 2026-05-12 run.
    today = datetime.now(UTC).date()
    # Today's receipt: +800 cash in
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=today,
        dr_ledger=bank,
        cr_ledger=cust,
        amount=Decimal("800.00"),
    )
    # Today's payment: 300 cash out
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Payment,
        on_date=today,
        dr_ledger=supp,
        cr_ledger=bank,
        amount=Decimal("300.00"),
    )
    # Optional voucher (must NOT contribute)
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=today,
        dr_ledger=bank,
        cr_ledger=cust,
        amount=Decimal("9999.00"),
        is_optional=True,
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert Decimal(body["today"]["cash_in"]) == Decimal("800.00")
    assert Decimal(body["today"]["cash_out"]) == Decimal("300.00")


def test_dashboard_today_voucher_counts(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, bank, cust, supp, sales, purchase = _seed(db_session)
    today = datetime.now(UTC).date()
    # 1 normal sale today
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=today,
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("1000.00"),
    )
    # 1 optional sale awaiting approval today
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=today,
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("500.00"),
        is_optional=True,
    )
    # 1 cancelled voucher today — must not be counted
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=today,
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("250.00"),
        status_=VoucherStatus.cancelled,
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert body["today"]["vouchers_created"] == 2
    assert body["today"]["vouchers_pending_approval"] == 1


def test_dashboard_outstanding_totals(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, bank, cust, supp, sales, purchase = _seed(db_session)
    today = datetime.now(UTC).date()
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=today,
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("1500.00"),
    )
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Purchase,
        on_date=today,
        dr_ledger=purchase,
        cr_ledger=supp,
        amount=Decimal("2200.00"),
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert Decimal(body["outstanding"]["receivables_total"]) == Decimal(
        "1500.00"
    )
    assert Decimal(body["outstanding"]["payables_total"]) == Decimal("2200.00")


def test_dashboard_gst_liability_mtd_is_output_minus_input(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, bank, cust, supp, sales, purchase = _seed(db_session)
    today = datetime.now(UTC).date()
    # Output GST 180 (sale)
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=today,
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("1000.00"),
        gst_applicable=True,
        cgst=Decimal("90.00"),
        sgst=Decimal("90.00"),
    )
    # Input GST 50 (purchase)
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Purchase,
        on_date=today,
        dr_ledger=purchase,
        cr_ledger=supp,
        amount=Decimal("500.00"),
        gst_applicable=True,
        cgst=Decimal("25.00"),
        sgst=Decimal("25.00"),
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert Decimal(body["gst_liability_indicative"]["month_to_date"]) == (
        Decimal("130.00")
    )


def test_dashboard_gst_liability_floors_at_zero_when_input_exceeds_output(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, bank, cust, supp, sales, purchase = _seed(db_session)
    today = datetime.now(UTC).date()
    # Input only — no sales side this month
    _voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Purchase,
        on_date=today,
        dr_ledger=purchase,
        cr_ledger=supp,
        amount=Decimal("1000.00"),
        gst_applicable=True,
        cgst=Decimal("50.00"),
        sgst=Decimal("50.00"),
    )
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    body = r.json()
    assert Decimal(body["gst_liability_indicative"]["month_to_date"]) == (
        Decimal("0")
    )


def test_dashboard_response_shape_smoke(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, *_ = _seed(db_session)
    r = client.get("/api/v1/dashboard/home", headers=_h(user, company))
    assert r.status_code == 200, r.text
    body = r.json()
    # Verify the top-level keys exist exactly per REPORTS.md.
    for key in (
        "as_of",
        "company_name",
        "connector",
        "today",
        "this_month",
        "outstanding",
        "gst_liability_indicative",
        "alerts",
    ):
        assert key in body, f"missing key: {key}"
