"""Integration tests for GET /api/v1/reports/balance-sheet (P0.38)."""

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


def _voucher(  # type: ignore[no-untyped-def]
    db: Session,
    company_id,
    *,
    voucher_type: VoucherType,
    on_date: date,
    dr_ledger,
    cr_ledger,
    amount: Decimal,
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


def _seed(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.viewer)
    capital = Ledger(
        company_id=company.id,
        name="Capital",
        name_normalized="capital",
        group_name="Capital Account",
        balance_type=BalanceType.Cr,
        opening_balance=Decimal("10000.00"),
    )
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
        opening_balance=Decimal("10000.00"),
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
    db.add_all([capital, bank, cust, sales])
    db.commit()
    # 3000 sale on credit. After this: Acme Dr 3000, Sales Cr 3000.
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 4, 10),
        dr_ledger=cust,
        cr_ledger=sales,
        amount=Decimal("3000.00"),
    )
    return user, company


def test_balance_sheet_equation_holds(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/balance-sheet?as_of_date=2026-04-30",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["equation"]["in_balance"] is True
    # Assets: Bank 10000 + Acme 3000 = 13000.
    # Liabilities: Capital 10000.
    # P&L: Sales 3000 (profit) → equity 3000. Total liab+equity 13000.
    assert Decimal(body["equation"]["assets"]) == Decimal("13000.00")
    assert Decimal(body["equation"]["liabilities_plus_equity"]) == Decimal(
        "13000.00"
    )
    assert body["current_period_profit_loss"]["type"] == "profit"
    assert Decimal(body["current_period_profit_loss"]["value"]) == Decimal(
        "3000.00"
    )


def test_balance_sheet_groups_split_correctly(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/balance-sheet?as_of_date=2026-04-30",
        headers=_h(user, company),
    )
    body = r.json()
    asset_groups = {g["group_name"] for g in body["assets"]["groups"]}
    liability_groups = {g["group_name"] for g in body["liabilities"]["groups"]}
    assert "Bank Accounts" in asset_groups
    assert "Sundry Debtors" in asset_groups
    assert "Capital Account" in liability_groups
    # Sales ledger lives in Sales Accounts — that's an Income group, so
    # it must NOT appear on the balance sheet directly (it's folded
    # into the current-period P&L instead).
    assert "Sales Accounts" not in asset_groups
    assert "Sales Accounts" not in liability_groups


def test_balance_sheet_allows_negative_contra_balance(
    client: TestClient, db_session: Session
) -> None:
    """A Sundry Debtor with a *credit* balance yields a negative
    balance-sheet line. This regressed as a 500 when the line/total
    fields were typed `Money` (>= 0) instead of `SignedMoney`: the
    balance sheet carries its sign in the value, so contra-balances
    are legitimate. See §7.6 reports validation 2026-07-28.
    """
    db = db_session
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
    debtor = Ledger(
        company_id=company.id,
        name="Xyz Ltd",
        name_normalized="xyz ltd",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    db.add_all([bank, debtor])
    db.commit()
    # Receipt: Bank Dr 1000 / Xyz Ltd Cr 1000. With no opening receivable,
    # the debtor ends the period with a 1000 *credit* balance -> -1000 on
    # the assets side.
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=date(2026, 4, 10),
        dr_ledger=bank,
        cr_ledger=debtor,
        amount=Decimal("1000.00"),
    )
    r = client.get(
        "/api/v1/reports/balance-sheet?as_of_date=2026-04-30",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    debtor_group = next(
        g
        for g in body["assets"]["groups"]
        if g["group_name"] == "Sundry Debtors"
    )
    assert Decimal(debtor_group["total"]) == Decimal("-1000.00")
    line = next(
        line
        for line in debtor_group["ledgers"]
        if line["ledger_name"] == "Xyz Ltd"
    )
    assert Decimal(line["amount"]) == Decimal("-1000.00")
    # Bank +1000 nets the debtor -1000 -> assets total 0, equation holds.
    assert Decimal(body["assets"]["total"]) == Decimal("0.00")
    assert body["equation"]["in_balance"] is True
