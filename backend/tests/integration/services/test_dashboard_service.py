"""build_dashboard's "today" boundary is IST-aware, not UTC (P3.2 sibling
fix -- see release/TAXMIND-PILOT-READINESS-2026-08-12.md §18/§20).

Proves the wiring, not just the pure function (app/core/company_time.py
already has its own focused unit tests): a voucher dated "today in IST"
must count toward `today` metrics even when the wall-clock instant is
still "yesterday" by the UTC calendar date -- the exact 00:00-05:29 IST
window the bug report names.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.company import Company
from app.models.voucher import Voucher, VoucherStatus, VoucherType
from app.services.dashboard_service import build_dashboard
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company


def _voucher(company: Company, *, on: date, amount: str = "100.00") -> Voucher:
    return Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Receipt,
        date=on,
        total_amount=Decimal(amount),
        status=VoucherStatus.posted,
    )


def test_dashboard_today_uses_ist_not_utc_date(db_session: Session) -> None:
    company = make_company(db_session)  # default timezone: Asia/Kolkata
    # 2026-09-15T23:00:00Z is 2026-09-16 04:30 IST -- deep in the documented
    # bug window. A voucher dated 2026-09-16 (the correct IST "today")
    # must be counted; the old UTC-date logic would have used
    # 2026-09-15 and missed it.
    now = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)
    v = _voucher(company, on=date(2026, 9, 16))
    db_session.add(v)
    db_session.commit()

    data = build_dashboard(db_session, company=company, now=now)

    assert data.today.vouchers_created == 1


def test_dashboard_today_excludes_ist_yesterday_during_the_bug_window(
    db_session: Session,
) -> None:
    company = make_company(db_session)
    now = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)  # 2026-09-16 04:30 IST
    # Dated for the UTC calendar day, which is already yesterday in IST at
    # this instant -- must NOT be counted in "today".
    v = _voucher(company, on=date(2026, 9, 15))
    db_session.add(v)
    db_session.commit()

    data = build_dashboard(db_session, company=company, now=now)

    assert data.today.vouchers_created == 0
