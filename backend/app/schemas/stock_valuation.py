"""Schemas for per-financial-year stock valuations."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from app.schemas.common import SignedMoney, TaxMindBooksBase


class StockValuationOut(TaxMindBooksBase):
    """Opening/closing stock VALUE for one financial year, Dr positive.

    A negative value is a net credit (Tally's negative stock) and is shown
    faithfully, never clamped.
    """

    id: UUID
    label: str  # "FY 2025-26"
    period_from: date
    period_to: date
    opening_value: SignedMoney
    closing_value: SignedMoney
    source: str  # "tally" | "manual"
    # Data-quality context from the Tally pull (null for manual entries):
    # how many stock items exist, and how many have a NEGATIVE closing
    # quantity (issued more than received) -- worth a bookkeeper's review.
    item_count: int | None = None
    negative_stock_items: int | None = None
    captured_at: datetime


class StockValuationListResponse(TaxMindBooksBase):
    items: list[StockValuationOut]


class StockValuationManualRequest(TaxMindBooksBase):
    """Body of ``PUT /stock-valuations/{period_from}`` (manual entry)."""

    opening_value: SignedMoney
    closing_value: SignedMoney
