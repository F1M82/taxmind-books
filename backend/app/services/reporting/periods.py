"""Selectable financial years for the report period picker.

The years a company can be viewed for are driven by its data, not guessed:
from the earliest of (opening-balance anchor, earliest voucher date) to the
latest of (today, latest voucher date). Indian FY: 1 April to 31 March.
Newest first. The current year's ``to_date`` is clipped to today so
as-of reports default to "now" rather than a future 31 March.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.voucher import Voucher
from app.services.reporting.profit_loss import fiscal_year_start


@dataclass(frozen=True)
class FinancialYear:
    label: str  # "FY 2025-26"
    from_date: date
    to_date: date
    is_current: bool


def _label(start_year: int) -> str:
    return f"FY {start_year}-{str(start_year + 1)[-2:]}"


def financial_years(  # audit-exempt: read-only SELECT of voucher date bounds
    db: Session,
    *,
    company_id: UUID,
    anchor_date: date | None,
    today: date,
) -> list[FinancialYear]:
    """Financial years to offer, newest first (always includes the current)."""
    first_v, last_v = db.execute(
        select(func.min(Voucher.date), func.max(Voucher.date)).where(
            Voucher.company_id == company_id
        )
    ).one()
    starts = [d for d in (anchor_date, first_v) if d is not None]
    earliest = min(starts) if starts else today
    latest = max([today] + ([last_v] if last_v is not None else []))

    first_year = fiscal_year_start(min(earliest, today)).year
    last_year = fiscal_year_start(latest).year
    current_year = fiscal_year_start(today).year

    years: list[FinancialYear] = []
    for start_year in range(last_year, first_year - 1, -1):
        fy_end = date(start_year + 1, 3, 31)
        is_current = start_year == current_year
        years.append(
            FinancialYear(
                label=_label(start_year),
                from_date=date(start_year, 4, 1),
                to_date=min(fy_end, today) if is_current else fy_end,
                is_current=is_current,
            )
        )
    return years
