"""Integration tests for GET /api/v1/dashboard/financials."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.reporting.profit_loss import fiscal_year_start
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


def _ledger(db, company_id, name, group):  # type: ignore[no-untyped-def]
    led = Ledger(
        company_id=company_id,
        name=name,
        name_normalized=name.lower(),
        group_name=group,
    )
    db.add(led)
    db.flush()
    return led


def _vch(  # type: ignore[no-untyped-def]
    db, company_id, *, vt, on_date, dr, cr, amount
):
    v = Voucher(
        company_id=company_id,
        voucher_type=vt,
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
                ledger_id=dr.id,
                amount=amount,
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company_id,
                voucher_id=v.id,
                ledger_id=cr.id,
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
    make_membership(db, user, company, role=CompanyRole.owner)
    bank = _ledger(db, company.id, "Bank", "Bank Accounts")
    sales = _ledger(db, company.id, "Sales", "Sales Accounts")
    purchase = _ledger(db, company.id, "Purchase", "Purchase Accounts")
    rent = _ledger(db, company.id, "Rent", "Indirect Expenses")
    db.commit()
    # Sales 10000 (Bank Dr / Sales Cr)
    _vch(
        db, company.id, vt=VoucherType.Sales,
        on_date=date(2026, 5, 10), dr=bank, cr=sales, amount=Decimal("10000.00"),
    )
    # Purchase 4000 (Purchase Dr / Bank Cr)
    _vch(
        db, company.id, vt=VoucherType.Purchase,
        on_date=date(2026, 5, 12), dr=purchase, cr=bank, amount=Decimal("4000.00"),
    )
    # Rent expense 1000 (Rent Dr / Bank Cr)
    _vch(
        db, company.id, vt=VoucherType.Payment,
        on_date=date(2026, 5, 15), dr=rent, cr=bank, amount=Decimal("1000.00"),
    )
    return user, company


def test_dashboard_financials_computes(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/dashboard/financials?from=2026-04-01&to=2026-06-30",
        headers=_h(user, company),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["sales"]) == Decimal("10000.00")
    assert Decimal(body["purchase"]) == Decimal("4000.00")
    # Expenses = operating (rent) only, NOT purchases.
    assert Decimal(body["expenses"]) == Decimal("1000.00")
    # Net profit = income 10000 - expense (purchase 4000 + rent 1000) = 5000.
    assert body["net_profit"]["type"] == "profit"
    assert Decimal(body["net_profit"]["value"]) == Decimal("5000.00")
    assert body["from_date"] == "2026-04-01"
    assert body["to_date"] == "2026-06-30"


def test_dashboard_financials_defaults_to_current_fy(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    r = client.get(
        "/api/v1/dashboard/financials", headers=_h(user, company)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # No params → period is the current Indian FY (April 1 → today).
    assert body["from_date"] == fiscal_year_start(date.today()).isoformat()
    assert body["to_date"] == date.today().isoformat()


def test_dashboard_financials_requires_company(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.get(
        "/api/v1/dashboard/financials",
        headers={"Authorization": f"Bearer {issue_token(user)}"},
    )
    # Missing X-Company-ID header.
    assert r.status_code == 422
