"""Integration tests for GET /api/v1/reports/outstanding (P0.38)."""

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
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
    )
    debtor_a = Ledger(
        company_id=company.id,
        name="Acme",
        name_normalized="acme",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
        gstin="27AAAAA1234A1Z5",
    )
    debtor_b = Ledger(
        company_id=company.id,
        name="Beta",
        name_normalized="beta",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    creditor = Ledger(
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
    db.add_all([bank, debtor_a, debtor_b, creditor, sales, purchase])
    db.commit()
    # Acme owes 1000 (1500 sale, 500 received)
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 4, 1),
        dr_ledger=debtor_a,
        cr_ledger=sales,
        amount=Decimal("1500.00"),
    )
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Receipt,
        on_date=date(2026, 4, 10),
        dr_ledger=bank,
        cr_ledger=debtor_a,
        amount=Decimal("500.00"),
    )
    # Beta owes 2000
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Sales,
        on_date=date(2026, 4, 5),
        dr_ledger=debtor_b,
        cr_ledger=sales,
        amount=Decimal("2000.00"),
    )
    # We owe Supplier 4000
    _voucher(
        db,
        company.id,
        voucher_type=VoucherType.Purchase,
        on_date=date(2026, 4, 8),
        dr_ledger=purchase,
        cr_ledger=creditor,
        amount=Decimal("4000.00"),
    )
    return user, company


def test_outstanding_receivables_lists_debtors_only(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/outstanding?type=receivables&as_of_date=2026-04-30",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    names = {item["ledger_name"] for item in body["items"]}
    assert names == {"Acme", "Beta"}
    by_name = {item["ledger_name"]: item for item in body["items"]}
    assert Decimal(by_name["Acme"]["balance"]) == Decimal("1000.00")
    assert by_name["Acme"]["balance_type"] == "Dr"
    assert by_name["Acme"]["ledger_gstin"] == "27AAAAA1234A1Z5"
    assert Decimal(by_name["Beta"]["balance"]) == Decimal("2000.00")
    # Total receivables 3000 Dr
    assert Decimal(body["total"]) == Decimal("3000.00")
    assert body["total_type"] == "Dr"


def test_outstanding_payables_lists_creditors_only(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/outstanding?type=payables&as_of_date=2026-04-30",
        headers=_h(user, company),
    )
    body = r.json()
    names = {item["ledger_name"] for item in body["items"]}
    assert names == {"Supp"}
    assert Decimal(body["total"]) == Decimal("4000.00")
    assert body["total_type"] == "Cr"


def test_outstanding_rejects_unknown_type(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/reports/outstanding?type=other",
        headers=_h(user, company),
    )
    assert r.status_code == 422
