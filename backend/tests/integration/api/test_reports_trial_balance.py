"""Integration tests for GET /api/v1/reports/trial-balance (P0.38)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.company import CompanyRole
from app.models.ledger import BalanceType, Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
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


def _post_voucher(  # type: ignore[no-untyped-def]
    db: Session,
    company_id,
    *,
    voucher_type: VoucherType,
    on_date: date,
    dr_ledger,
    cr_ledger,
    amount: Decimal,
    status_: VoucherStatus = VoucherStatus.posted,
    is_optional: bool = False,
) -> Voucher:
    v = Voucher(
        company_id=company_id,
        voucher_type=voucher_type,
        date=on_date,
        total_amount=amount,
        status=status_,
        source="manual",
        is_auto_posted=False,
        gst_applicable=False,
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
    db.refresh(v)
    return v


def _seed_company_with_three_vouchers(  # type: ignore[no-untyped-def]
    db: Session,
):
    user = make_user(db)
    company = make_company(db)
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
    sales = Ledger(
        company_id=company.id,
        name="Sales",
        name_normalized="sales",
        group_name="Sales Accounts",
        balance_type=BalanceType.Cr,
    )
    db.add_all([bank, cust, sales])
    db.commit()
    # Sale on credit (Acme Dr, Sales Cr) ₹1,000
    _post_voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 5, 1),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("1000.00"),
    )
    # Receipt: customer pays ₹600 (Bank Dr, Acme Cr)
    _post_voucher(
        db,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=date(2026, 5, 5),
        dr_ledger=bank,
        cr_ledger=cust,
        amount=Decimal("600.00"),
    )
    # Cancelled sale (should be excluded by R2)
    _post_voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 5, 4),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("9999.00"),
        status_=VoucherStatus.cancelled,
    )
    # Optional sale (should be excluded by R1)
    _post_voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 5, 3),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("500.00"),
        is_optional=True,
    )
    return user, company, bank, cust, sales


def test_trial_balance_returns_balanced_totals(
    client: TestClient, db_session: Session
) -> None:
    user, company, *_ = _seed_company_with_three_vouchers(db_session)
    r = client.get(
        "/api/v1/reports/trial-balance?as_of_date=2026-05-31",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["in_balance"] is True
    assert body["totals"]["total_dr"] == body["totals"]["total_cr"]
    # 1000 sale → Acme Dr 1000, Sales Cr 1000. Receipt 600 → Bank Dr 600,
    # Acme Cr 600. Acme net Dr 400; Bank Dr 600; Sales Cr 1000. Totals
    # Dr 1000, Cr 1000.
    assert Decimal(body["totals"]["total_dr"]) == Decimal("1000.00")


def test_trial_balance_excludes_optional_and_cancelled(
    client: TestClient, db_session: Session
) -> None:
    user, company, *_ = _seed_company_with_three_vouchers(db_session)
    r = client.get(
        "/api/v1/reports/trial-balance?as_of_date=2026-05-31",
        headers=_h(user, company),
    )
    body = r.json()
    assert body["exclusions"]["optional_vouchers_excluded_count"] == 1
    assert body["exclusions"]["cancelled_vouchers_excluded_count"] == 1
    by_name = {lg["ledger_name"]: lg for lg in body["ledgers"]}
    # Without exclusions, Sales would credit 1500 and Acme would Dr 1500;
    # exclusion makes Sales 1000 and Acme net Dr 400.
    assert Decimal(by_name["Sales"]["closing_balance"]) == Decimal("1000.00")
    assert by_name["Sales"]["closing_balance_type"] == "Cr"
    assert Decimal(by_name["Acme"]["closing_balance"]) == Decimal("400.00")
    assert by_name["Acme"]["closing_balance_type"] == "Dr"


def test_trial_balance_default_as_of_date_is_today(
    client: TestClient, db_session: Session
) -> None:
    user, company, *_ = _seed_company_with_three_vouchers(db_session)
    r = client.get(
        "/api/v1/reports/trial-balance",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    body = r.json()
    # We seeded dates in May 2026; today is well after, so all eligible
    # vouchers fall under the default range.
    assert body["totals"]["in_balance"] is True


def test_trial_balance_respects_date_cutoff(
    client: TestClient, db_session: Session
) -> None:
    user, company, *_ = _seed_company_with_three_vouchers(db_session)
    # Cut off before the receipt (2026-05-05) — only the 1000 sale lands.
    r = client.get(
        "/api/v1/reports/trial-balance?as_of_date=2026-05-04",
        headers=_h(user, company),
    )
    body = r.json()
    by_name = {lg["ledger_name"]: lg for lg in body["ledgers"]}
    assert "Bank" not in by_name  # no activity before this date
    assert Decimal(by_name["Acme"]["closing_balance"]) == Decimal("1000.00")


def test_trial_balance_includes_pending_tally_post_vouchers(
    client: TestClient, db_session: Session
) -> None:
    """P0.46d: a voucher queued for Tally is still live in the books.
    Trial balance must include it; otherwise the user's report goes
    blank when Tally is offline."""
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.viewer)
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
    )
    cust = Ledger(
        company_id=company.id,
        name="QueuedAcme",
        name_normalized="queuedacme",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    db_session.add_all([bank, cust])
    db_session.commit()
    _post_voucher(
        db_session,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=date(2026, 5, 8),
        dr_ledger=bank,
        cr_ledger=cust,
        amount=Decimal("750.00"),
        status_=VoucherStatus.pending_tally_post,
    )
    r = client.get(
        "/api/v1/reports/trial-balance?as_of_date=2026-05-31",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {lg["ledger_name"]: lg for lg in body["ledgers"]}
    assert Decimal(by_name["Bank"]["closing_balance"]) == Decimal("750.00")


def test_trial_balance_tenant_isolated(
    client: TestClient, db_session: Session
) -> None:
    user_a, company_a, *_ = _seed_company_with_three_vouchers(db_session)
    user_b, company_b, *_ = _seed_company_with_three_vouchers(db_session)
    r = client.get(
        f"/api/v1/reports/trial-balance?as_of_date={date.today().isoformat()}",
        headers=_h(user_a, company_a),
    )
    body = r.json()
    # No ledger from B should appear under A's report. Both companies
    # share ledger NAMES; the discriminator is the UUID.
    a_ids = {lg["ledger_id"] for lg in body["ledgers"]}
    b_ids = {
        str(lid)
        for (lid,) in db_session.query(Ledger.id)
        .filter(Ledger.company_id == company_b.id)
        .all()
    }
    assert a_ids.isdisjoint(b_ids)
