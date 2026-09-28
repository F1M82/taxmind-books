"""Stock valuation lookups for the P&L and balance sheet.

Values come from ``stock_valuations`` (one row per financial year, signed
Dr-positive). Tally's own definition ``opening(FY n+1) == closing(FY n)``
means a window spanning several years needs only the opening of its first
year and the closing of its last -- intermediate rows are not required.

A window is only adjusted when it is exactly the shape a valuation
describes, so a report never mixes a partial period with a whole-year
stock figure:

* it starts on an FY start (1 April), and
* it ends on an FY end (31 March) or, for the current year, on/after today
  (the stored closing is "as of capture", which for the current year is now).

Anything else, or a missing valuation, returns ``None`` and the report is
exactly what it was before stock valuation existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stock_valuation import StockValuation


@dataclass(frozen=True)
class StockEffect:
    opening_value: Decimal  # stock at window start, Dr positive
    closing_value: Decimal  # stock at window end, Dr positive
    source: str  # source of the closing valuation: "tally" | "manual"
    captured_at: datetime  # when the closing valuation was recorded

    @property
    def net(self) -> Decimal:
        """Effect on profit: closing stock adds to it, opening stock costs."""
        return self.closing_value - self.opening_value


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_start(start_year: int) -> date:
    return date(start_year, 4, 1)


def fy_end(start_year: int) -> date:
    return date(start_year + 1, 3, 31)


def load_valuations(  # audit-exempt: read-only SELECT
    db: Session, company_id: UUID
) -> dict[int, StockValuation]:
    """All of a company's valuations keyed by FY start year."""
    rows = db.execute(
        select(StockValuation).where(StockValuation.company_id == company_id)
    ).scalars()
    return {fy_start_year(r.period_from): r for r in rows}


def _end_is_applicable(to_date: date, today: date) -> bool:
    year = fy_start_year(to_date)
    return to_date >= min(fy_end(year), today)


def stock_effect_for_window(  # audit-exempt: read-only lookup
    db: Session,
    *,
    company_id: UUID,
    from_date: date,
    to_date: date,
    today: date,
    valuations: dict[int, StockValuation] | None = None,
) -> StockEffect | None:
    """Stock effect for ``[from_date, to_date]``, or None if not applicable."""
    if from_date != fy_start(fy_start_year(from_date)):
        return None
    if not _end_is_applicable(to_date, today):
        return None
    first, last = fy_start_year(from_date), fy_start_year(to_date)
    if first > last:
        return None
    vals = (
        valuations if valuations is not None else load_valuations(db, company_id)
    )
    opening_row, closing_row = vals.get(first), vals.get(last)
    if opening_row is None or closing_row is None:
        return None
    return StockEffect(
        opening_value=opening_row.opening_value,
        closing_value=closing_row.closing_value,
        source=closing_row.source,
        captured_at=closing_row.captured_at,
    )


@dataclass(frozen=True)
class BalanceSheetStock:
    """Everything the balance sheet needs to apply stock consistently."""

    closing_value: Decimal  # Stock-in-hand asset as of the sheet date (Dr+)
    current_net: Decimal  # closing - opening of the sheet's financial year
    prior_net: Decimal  # net stock effect of all earlier years


def balance_sheet_stock(  # audit-exempt: read-only lookup
    db: Session,
    *,
    company_id: UUID,
    as_of_date: date,
    anchor_date: date | None,
    today: date,
) -> BalanceSheetStock | None:
    """Stock figures for a balance sheet, or None if they can't be applied
    completely (all-or-nothing, so the sheet stays balanced)."""
    if anchor_date is None:
        return None
    vals = load_valuations(db, company_id)
    year = fy_start_year(as_of_date)
    first_year = fy_start_year(anchor_date)
    if year < first_year:
        return None

    current = stock_effect_for_window(
        db,
        company_id=company_id,
        from_date=fy_start(year),
        to_date=as_of_date,
        today=today,
        valuations=vals,
    )
    if current is None:
        return None

    prior_net = Decimal("0")
    if year > first_year:
        prior = stock_effect_for_window(
            db,
            company_id=company_id,
            from_date=fy_start(first_year),
            to_date=fy_end(year - 1),
            today=today,
            valuations=vals,
        )
        if prior is None:
            return None
        prior_net = prior.net
    return BalanceSheetStock(
        closing_value=current.closing_value,
        current_net=current.net,
        prior_net=prior_net,
    )
