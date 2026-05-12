"""Profit & Loss per docs/REPORTS.md §"Profit & Loss".

Income = period movement of ledgers under INCOME_GROUPS (Cr-natured;
positive value = credit to income ledger).
Expense = period movement of ledgers under EXPENSE_GROUPS (Dr-natured).

The P&L is a *period* report — it answers "what was the result between
from_date and to_date". Unlike the trial balance, opening balances are
not folded in. Income and expense ledgers are reset every financial
year at year-end in Tally; the API treats `from_date` as the period
start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ledger import Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
)
from app.services.reporting.tally_groups import EXPENSE_GROUPS, INCOME_GROUPS


@dataclass
class PnLLedgerLine:
    ledger_id: UUID
    ledger_name: str
    group_name: str | None
    amount: Decimal


@dataclass
class PnLSection:
    ledgers: list[PnLLedgerLine] = field(default_factory=list)
    total: Decimal = Decimal("0")


@dataclass
class ProfitLossResult:
    from_date: date
    to_date: date
    income: PnLSection
    expense: PnLSection
    net_value: Decimal
    net_type: Literal["profit", "loss"]


def _movements_for_groups(  # audit-exempt: read-only SELECT aggregation
    db: Session,
    *,
    company_id: UUID,
    from_date: date,
    to_date: date,
    groups: frozenset[str],
) -> list[tuple[UUID, str, str | None, Decimal, Decimal]]:
    """Return (ledger_id, name, group_name, sum_dr, sum_cr) for each
    ledger whose normalized group_name is in `groups` and that had
    activity in [from_date, to_date]."""
    dr_expr = case(
        (LedgerEntry.entry_type == EntryType.Dr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    cr_expr = case(
        (LedgerEntry.entry_type == EntryType.Cr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    stmt = (
        select(
            Ledger.id,
            Ledger.name,
            Ledger.group_name,
            func.coalesce(func.sum(dr_expr), Decimal("0")).label("sum_dr"),
            func.coalesce(func.sum(cr_expr), Decimal("0")).label("sum_cr"),
        )
        .join(LedgerEntry, LedgerEntry.ledger_id == Ledger.id)
        .join(Voucher, Voucher.id == LedgerEntry.voucher_id)
        .where(
            Ledger.company_id == company_id,
            LedgerEntry.company_id == company_id,
            Voucher.company_id == company_id,
            Voucher.date >= from_date,
            Voucher.date <= to_date,
            Voucher.status == VoucherStatus.posted,
            Voucher.is_optional_in_tally.is_(False),
            func.lower(func.trim(Ledger.group_name)).in_(groups),
        )
        .group_by(Ledger.id, Ledger.name, Ledger.group_name)
    )
    return [
        (r.id, r.name, r.group_name, Decimal(r.sum_dr), Decimal(r.sum_cr))
        for r in db.execute(stmt).all()
    ]


def compute_profit_loss(
    db: Session,
    *,
    company_id: UUID,
    from_date: date,
    to_date: date,
) -> ProfitLossResult:
    """Compute P&L over [from_date, to_date]."""
    # Income ledgers are Cr-natured: net income contribution = Cr - Dr.
    income = PnLSection()
    for lid, name, grp, sum_dr, sum_cr in _movements_for_groups(
        db,
        company_id=company_id,
        from_date=from_date,
        to_date=to_date,
        groups=INCOME_GROUPS,
    ):
        amount = sum_cr - sum_dr
        if amount == 0:
            continue
        income.ledgers.append(
            PnLLedgerLine(
                ledger_id=lid, ledger_name=name, group_name=grp, amount=amount
            )
        )
        income.total += amount

    # Expense ledgers are Dr-natured: net expense = Dr - Cr.
    expense = PnLSection()
    for lid, name, grp, sum_dr, sum_cr in _movements_for_groups(
        db,
        company_id=company_id,
        from_date=from_date,
        to_date=to_date,
        groups=EXPENSE_GROUPS,
    ):
        amount = sum_dr - sum_cr
        if amount == 0:
            continue
        expense.ledgers.append(
            PnLLedgerLine(
                ledger_id=lid, ledger_name=name, group_name=grp, amount=amount
            )
        )
        expense.total += amount

    net = income.total - expense.total
    net_value = abs(net)
    net_type: Literal["profit", "loss"] = "profit" if net >= 0 else "loss"

    income.ledgers.sort(key=lambda x: x.ledger_name)
    expense.ledgers.sort(key=lambda x: x.ledger_name)

    return ProfitLossResult(
        from_date=from_date,
        to_date=to_date,
        income=income,
        expense=expense,
        net_value=net_value,
        net_type=net_type,
    )


# ---------------------------------------------------------------------
# FY helper used by balance sheet
# ---------------------------------------------------------------------


def fiscal_year_start(d: date) -> date:
    """First day of the Indian FY containing `d`. April 1 cut-over."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)
