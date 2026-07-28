"""Pydantic schemas for the report endpoints (P0.38).

Shapes mirror docs/REPORTS.md exactly. Money fields are serialized as
Decimal strings per R5/MONEY.md; date fields as ISO-8601.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from app.schemas.common import Money, SignedMoney, TaxMindBooksBase

DrCr = Literal["Dr", "Cr"]
ProfitLoss = Literal["profit", "loss"]


# ---------------------------------------------------------------------
# Trial Balance
# ---------------------------------------------------------------------


class TrialBalanceLedger(TaxMindBooksBase):
    ledger_id: UUID
    ledger_name: str
    group_name: str | None = None
    opening_balance: Money
    opening_balance_type: DrCr
    period_dr: Money
    period_cr: Money
    closing_balance: Money
    closing_balance_type: DrCr


class TrialBalanceTotals(TaxMindBooksBase):
    total_dr: Money
    total_cr: Money
    in_balance: bool


class TrialBalanceExclusions(TaxMindBooksBase):
    optional_vouchers_excluded_count: int
    cancelled_vouchers_excluded_count: int


class TrialBalanceResponse(TaxMindBooksBase):
    as_of_date: date
    company_id: UUID
    ledgers: list[TrialBalanceLedger]
    totals: TrialBalanceTotals
    exclusions: TrialBalanceExclusions


# ---------------------------------------------------------------------
# Profit & Loss
# ---------------------------------------------------------------------


class PnLLedger(TaxMindBooksBase):
    ledger_id: UUID
    ledger_name: str
    amount: Money


class PnLSection(TaxMindBooksBase):
    ledgers: list[PnLLedger]
    total: Money


class PnLNet(TaxMindBooksBase):
    value: Money
    type: ProfitLoss


class ProfitLossResponse(TaxMindBooksBase):
    from_date: date
    to_date: date
    income: PnLSection
    expense: PnLSection
    net: PnLNet


# ---------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------


# Balance-sheet magnitudes carry their sign in the value itself (there is no
# accompanying Dr/Cr field like Trial Balance / Outstanding have), so a
# contra-balance — e.g. a Sundry Debtor with a credit balance — legitimately
# yields a negative amount. Use SignedMoney; plain Money (>= 0) 500s on it.
class BSLine(TaxMindBooksBase):
    ledger_id: UUID
    ledger_name: str
    amount: SignedMoney


class BSGroup(TaxMindBooksBase):
    group_name: str
    ledgers: list[BSLine]
    total: SignedMoney


class BSSection(TaxMindBooksBase):
    groups: list[BSGroup]
    total: SignedMoney


class BSPnL(TaxMindBooksBase):
    value: Money
    type: ProfitLoss


class BSEquation(TaxMindBooksBase):
    assets: SignedMoney
    liabilities_plus_equity: SignedMoney
    in_balance: bool


class BalanceSheetResponse(TaxMindBooksBase):
    as_of_date: date
    assets: BSSection
    liabilities: BSSection
    current_period_profit_loss: BSPnL
    equation: BSEquation


# ---------------------------------------------------------------------
# Outstanding
# ---------------------------------------------------------------------


class OutstandingItem(TaxMindBooksBase):
    ledger_id: UUID
    ledger_name: str
    ledger_gstin: str | None = None
    balance: Money
    balance_type: DrCr


class OutstandingResponse(TaxMindBooksBase):
    type: Literal["receivables", "payables"]
    as_of_date: date
    items: list[OutstandingItem]
    total: Money
    total_type: DrCr
