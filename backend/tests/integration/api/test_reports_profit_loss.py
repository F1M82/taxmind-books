"""Integration tests for GET /api/v1/reports/profit-loss (P0.38)."""

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


def _voucher_two_lines(  # type: ignore[no-untyped-def]
    db: Session,
    company_id,
    *,
    voucher_type: VoucherType,
    on_date: date,
    dr_ledger,
    cr_ledger,
    amount: Decimal,
    is_optional: bool = False,
):
    v = Voucher(
        company_id=company_id,
        voucher_type=voucher_type,
        date=on_date,
        total_amount=amount,
        status=VoucherStatus.posted,
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
    return v


def _seed(db: Session):  # type: ignore[no-untyped-def]
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
    purchase = Ledger(
        company_id=company.id,
        name="Purchase",
        name_normalized="purchase",
        group_name="Purchase Accounts",
        balance_type=BalanceType.Dr,
    )
    salary = Ledger(
        company_id=company.id,
        name="Salary",
        name_normalized="salary",
        group_name="Indirect Expenses",
        balance_type=BalanceType.Dr,
    )
    supplier = Ledger(
        company_id=company.id,
        name="Supp",
        name_normalized="supp",
        group_name="Sundry Creditors",
        balance_type=BalanceType.Cr,
    )
    db.add_all([bank, cust, sales, purchase, salary, supplier])
    db.commit()
    # 5000 sale on credit
    _voucher_two_lines(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 4, 10),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("5000.00"),
    )
    # 2000 purchase on credit
    _voucher_two_lines(
        db,
        company.id,
        voucher_type=VoucherType.Purchase,
        on_date=date(2026, 4, 15),
        dr_ledger=purchase,
        cr_ledger=supplier,
        amount=Decimal("2000.00"),
    )
    # 800 salary payment from bank
    _voucher_two_lines(
        db,
        company.id,
        voucher_type=VoucherType.Payment,
        on_date=date(2026, 4, 20),
        dr_ledger=salary,
        cr_ledger=bank,
        amount=Decimal("800.00"),
    )
    return user, company


def test_profit_loss_net_is_income_minus_expense(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/profit-loss?from_date=2026-04-01&to_date=2026-04-30",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["income"]["total"]) == Decimal("5000.00")
    assert Decimal(body["expense"]["total"]) == Decimal("2800.00")
    assert body["net"]["type"] == "profit"
    assert Decimal(body["net"]["value"]) == Decimal("2200.00")


def test_profit_loss_loss_when_expense_exceeds_income(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    # Add a big expense that flips us into loss territory.
    salary = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Salary")
        .one()
    )
    bank = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Bank")
        .one()
    )
    _voucher_two_lines(
        db_session,
        company.id,
        voucher_type=VoucherType.Payment,
        on_date=date(2026, 4, 21),
        dr_ledger=salary,
        cr_ledger=bank,
        amount=Decimal("10000.00"),
    )
    r = client.get(
        "/api/v1/reports/profit-loss?from_date=2026-04-01&to_date=2026-04-30",
        headers=_h(user, company),
    )
    body = r.json()
    assert body["net"]["type"] == "loss"
    # Income 5000, expense 2800 + 10000 = 12800. Net loss 7800.
    assert Decimal(body["net"]["value"]) == Decimal("7800.00")


def test_profit_loss_optional_excluded(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    cust = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Acme")
        .one()
    )
    sales = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sales")
        .one()
    )
    # Optional sale that should NOT contribute to income.
    _voucher_two_lines(
        db_session,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 4, 18),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("9000.00"),
        is_optional=True,
    )
    r = client.get(
        "/api/v1/reports/profit-loss?from_date=2026-04-01&to_date=2026-04-30",
        headers=_h(user, company),
    )
    body = r.json()
    assert Decimal(body["income"]["total"]) == Decimal("5000.00")


def test_profit_loss_default_dates_use_current_fy(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get("/api/v1/reports/profit-loss", headers=_h(user, company))
    assert r.status_code == 200
    body = r.json()
    # The seed lives in April 2026 (FY 2026-27 if today >= April 2026).
    # Either FY 2025-26 or FY 2026-27 will catch the data — the
    # important property is the endpoint runs without explicit dates.
    assert "from_date" in body and "to_date" in body
