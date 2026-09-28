"""Financial-year list for the report period picker (GET /reports/periods)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.models.company import CompanyRole
from app.models.voucher import Voucher
from app.services.reporting.periods import financial_years
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)

TODAY = date(2026, 9, 28)  # FY 2026-27


def _voucher(db: Session, company, day: date) -> None:  # type: ignore[no-untyped-def]
    db.add(
        Voucher(
            company_id=company.id,
            date=day,
            total_amount=Decimal("0"),
            tally_voucher_type="Journal",
            tally_guid=f"g-{uuid4().hex}",
        )
    )
    db.commit()


def _labels(years) -> list[str]:  # type: ignore[no-untyped-def]
    return [y.label for y in years]


def test_no_data_offers_only_current_year(db_session: Session) -> None:
    company = make_company(db_session)
    years = financial_years(
        db_session, company_id=company.id, anchor_date=None, today=TODAY
    )
    assert _labels(years) == ["FY 2026-27"]
    assert years[0].is_current
    assert years[0].from_date == date(2026, 4, 1)
    assert years[0].to_date == TODAY  # clipped to today, not 31 Mar 2027


def test_anchor_defines_first_year_newest_first(db_session: Session) -> None:
    company = make_company(db_session)
    years = financial_years(
        db_session,
        company_id=company.id,
        anchor_date=date(2023, 4, 1),
        today=TODAY,
    )
    assert _labels(years) == [
        "FY 2026-27",
        "FY 2025-26",
        "FY 2024-25",
        "FY 2023-24",
    ]
    past = years[1]
    assert (past.from_date, past.to_date) == (date(2025, 4, 1), date(2026, 3, 31))
    assert not past.is_current
    assert [y.is_current for y in years] == [True, False, False, False]


def test_anchor_mid_year_starts_in_its_own_fy(db_session: Session) -> None:
    company = make_company(db_session)
    years = financial_years(
        db_session,
        company_id=company.id,
        anchor_date=date(2025, 4, 1),
        today=date(2026, 3, 31),  # last day of FY 2025-26
    )
    assert _labels(years) == ["FY 2025-26"]
    assert years[0].to_date == date(2026, 3, 31)


def test_earlier_voucher_extends_range_below_anchor(db_session: Session) -> None:
    company = make_company(db_session)
    _voucher(db_session, company, date(2022, 8, 10))
    years = financial_years(
        db_session,
        company_id=company.id,
        anchor_date=date(2024, 4, 1),
        today=TODAY,
    )
    assert _labels(years)[-1] == "FY 2022-23"
    assert len(years) == 5


def test_future_dated_voucher_extends_range_above_today(
    db_session: Session,
) -> None:
    company = make_company(db_session)
    _voucher(db_session, company, date(2027, 5, 2))
    years = financial_years(
        db_session, company_id=company.id, anchor_date=None, today=TODAY
    )
    assert _labels(years) == ["FY 2027-28", "FY 2026-27"]
    # Only the calendar-current year is flagged and clipped.
    assert [y.is_current for y in years] == [False, True]
    assert years[0].to_date == date(2028, 3, 31)


def test_other_companys_vouchers_do_not_leak(db_session: Session) -> None:
    mine = make_company(db_session)
    other = make_company(db_session)
    _voucher(db_session, other, date(2019, 6, 1))
    years = financial_years(
        db_session, company_id=mine.id, anchor_date=None, today=TODAY
    )
    assert _labels(years) == ["FY 2026-27"]


def test_fy_boundaries_on_april_first(db_session: Session) -> None:
    company = make_company(db_session)
    years = financial_years(
        db_session,
        company_id=company.id,
        anchor_date=date(2025, 3, 31),  # last day of FY 2024-25
        today=date(2025, 4, 1),  # first day of FY 2025-26
    )
    assert _labels(years) == ["FY 2025-26", "FY 2024-25"]


# ---------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------


def _headers(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def test_endpoint_returns_years_for_a_viewer(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    company.opening_balance_anchor_date = date(2024, 4, 1)
    db_session.commit()
    make_membership(db_session, user, company, role=CompanyRole.viewer)
    r = client.get("/api/v1/reports/periods", headers=_headers(user, company))
    assert r.status_code == 200, r.json()
    items = r.json()["items"]
    assert items[0]["is_current"] is True
    assert len(items) >= 2  # FY of the anchor .. current
    assert items[-1]["from_date"] == "2024-04-01"
    assert set(items[0]) == {"label", "from_date", "to_date", "is_current"}


def test_endpoint_hides_company_from_non_members(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session)
    r = client.get("/api/v1/reports/periods", headers=_headers(user, company))
    assert r.status_code == 404
