"""Record per-financial-year stock valuations (Tally pull or manual entry).

See docs/PHASE_3_CLOSING_STOCK_DESIGN.md. Values are signed Dr-positive and
stored as given -- a net credit is never clamped.

Two safety rules keep the reports balanced:

* **One financial year per valuation.** Tally values stock for whatever
  period its gateway is scoped to; a period that is not exactly 1 April -
  31 March cannot be stored (the operator is told to set one year).
* **First-year opening must match the books.** For the financial year that
  contains the company's opening-balance anchor, the opening stock must equal
  the company's Stock-in-Hand ledger balance (the "Opening Stock" ledger that
  carries the seeded opening trial balance). Otherwise substituting Tally's
  closing stock for that ledger would unbalance the balance sheet.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import StockOpeningMismatch, StockPeriodNotSingleFY
from app.core.money import quantize_money
from app.models.company import Company
from app.models.ledger import BalanceType, Ledger
from app.models.stock_valuation import StockValuation
from app.services.reporting.stock import fy_end, fy_start, fy_start_year

_STOCK_GROUP = "stock-in-hand"


def _snapshot(v: StockValuation) -> dict[str, Any]:
    return {
        "period_from": v.period_from.isoformat(),
        "period_to": v.period_to.isoformat(),
        "opening_value": str(v.opening_value),
        "closing_value": str(v.closing_value),
        "source": v.source,
    }


def list_valuations(  # audit-exempt: read-only SELECT
    db: Session, company_id: UUID
) -> list[StockValuation]:
    """A company's valuations, newest financial year first."""
    return list(
        db.execute(
            select(StockValuation)
            .where(StockValuation.company_id == company_id)
            .order_by(StockValuation.period_from.desc())
        ).scalars()
    )


class StockValuationService:
    def __init__(
        self, db: Session, audit: AuditEmitter, company_id: UUID
    ) -> None:
        self.db = db
        self.audit = audit
        self.company_id = company_id

    def stock_ledger_balance(self) -> Decimal:  # audit-exempt: read-only SELECT
        """Signed (Dr positive) sum of the company's Stock-in-Hand ledgers."""
        rows = self.db.execute(
            select(Ledger.opening_balance, Ledger.balance_type).where(
                Ledger.company_id == self.company_id,
                func.lower(func.trim(Ledger.group_name)) == _STOCK_GROUP,
            )
        ).all()
        total = Decimal("0")
        for amount, kind in rows:
            total += amount if kind == BalanceType.Dr else -amount
        return total

    def record(
        self,
        *,
        period_from: date,
        period_to: date,
        opening_value: Decimal,
        closing_value: Decimal,
        source: str,
        item_count: int | None = None,
        negative_stock_items: int | None = None,
        captured_by: UUID | None = None,
    ) -> StockValuation:
        """Insert or replace the valuation for the financial year.

        Raises `StockPeriodNotSingleFY` / `StockOpeningMismatch`.
        """
        year = fy_start_year(period_from)
        if period_from != fy_start(year) or period_to != fy_end(year):
            raise StockPeriodNotSingleFY(
                "Stock can only be recorded for one financial year "
                "(1 April to 31 March). At the Gateway of Tally main menu "
                "press F2 and set the period to a single financial year.",
                details={
                    "period_from": period_from.isoformat(),
                    "period_to": period_to.isoformat(),
                },
            )

        opening_value = quantize_money(opening_value)
        closing_value = quantize_money(closing_value)

        company = self.db.get(Company, self.company_id)
        anchor = company.opening_balance_anchor_date if company else None
        if anchor is not None and fy_start_year(anchor) == year:
            ledger_balance = quantize_money(self.stock_ledger_balance())
            if ledger_balance != opening_value:
                raise StockOpeningMismatch(
                    "The opening stock for the company's first financial "
                    "year must equal its Stock-in-Hand ledger balance "
                    "(the seeded opening trial balance). Fix the ledger "
                    "or the figure first.",
                    details={
                        "opening_value": str(opening_value),
                        "stock_in_hand_ledger_balance": str(ledger_balance),
                    },
                )

        existing = self.db.execute(
            select(StockValuation).where(
                StockValuation.company_id == self.company_id,
                StockValuation.period_from == period_from,
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is None:
            row = StockValuation(
                company_id=self.company_id,
                period_from=period_from,
                period_to=period_to,
                opening_value=opening_value,
                closing_value=closing_value,
                source=source,
                item_count=item_count,
                negative_stock_items=negative_stock_items,
                captured_at=now,
                captured_by=captured_by,
            )
            self.db.add(row)
            self.db.flush()
            self.audit.emit(
                action="stock_valuation.recorded",
                entity_type="stock_valuation",
                entity_id=row.id,
                old_value=None,
                new_value=_snapshot(row),
            )
            return row

        old = _snapshot(existing)
        existing.opening_value = opening_value
        existing.closing_value = closing_value
        existing.source = source
        existing.item_count = item_count
        existing.negative_stock_items = negative_stock_items
        existing.captured_at = now
        existing.captured_by = captured_by
        self.db.flush()
        self.audit.emit(
            action="stock_valuation.recorded",
            entity_type="stock_valuation",
            entity_id=existing.id,
            old_value=old,
            new_value=_snapshot(existing),
        )
        return existing
