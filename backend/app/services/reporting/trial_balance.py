"""Trial balance computation per docs/REPORTS.md §"Trial Balance".

The trial balance is the closing balance of every active ledger in the
company as of `as_of_date`. Sum of Dr balances must equal sum of Cr
balances — if it doesn't, the data is corrupt and the caller should
surface a 500 with an alert.

R1: Optional vouchers (is_optional_in_tally=true AND no
    approved_to_regular_at) are excluded.
R2: Cancelled vouchers are excluded.
R3: voucher.date <= as_of_date (inclusive).
R6: Decimal arithmetic; quantize only at the boundary.
R8: company_id scoping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.ledger import BalanceType, Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
)


@dataclass
class TrialBalanceRow:
    ledger_id: UUID
    ledger_name: str
    group_name: str | None
    opening_balance: Decimal
    opening_balance_type: str
    period_dr: Decimal
    period_cr: Decimal
    closing_balance: Decimal
    closing_balance_type: str


@dataclass
class TrialBalanceResult:
    as_of_date: date
    company_id: UUID
    rows: list[TrialBalanceRow]
    total_dr: Decimal
    total_cr: Decimal
    optional_excluded_count: int
    cancelled_excluded_count: int

    @property
    def in_balance(self) -> bool:
        return self.total_dr == self.total_cr


# ---------------------------------------------------------------------
# Movement query
# ---------------------------------------------------------------------


def _period_movement_subquery(
    *,
    company_id: UUID,
    as_of_date: date,
) -> Any:
    """SUM(amount × sign) per ledger up to and including as_of_date.

    Returns a SQL subquery with columns `ledger_id, total_dr, total_cr`.
    Filters out Optional and cancelled vouchers per R1/R2.
    """
    dr_expr = case(
        (LedgerEntry.entry_type == EntryType.Dr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    cr_expr = case(
        (LedgerEntry.entry_type == EntryType.Cr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    return (
        select(
            LedgerEntry.ledger_id.label("ledger_id"),
            func.coalesce(func.sum(dr_expr), Decimal("0")).label("total_dr"),
            func.coalesce(func.sum(cr_expr), Decimal("0")).label("total_cr"),
        )
        .join(Voucher, Voucher.id == LedgerEntry.voucher_id)
        .where(
            LedgerEntry.company_id == company_id,
            Voucher.company_id == company_id,
            Voucher.date <= as_of_date,
            Voucher.status == VoucherStatus.posted,
            Voucher.is_optional_in_tally.is_(False),
        )
        .group_by(LedgerEntry.ledger_id)
        .subquery()
    )


def _ledger_balance_signed(
    opening: Decimal,
    opening_type: BalanceType,
    period_dr: Decimal,
    period_cr: Decimal,
) -> Decimal:
    """Closing balance as a signed Decimal (positive=Dr, negative=Cr)."""
    opening_signed = opening if opening_type is BalanceType.Dr else -opening
    return opening_signed + period_dr - period_cr


def _signed_to_pair(signed: Decimal) -> tuple[Decimal, str]:
    """Map a signed balance to (abs_value, 'Dr'|'Cr'). Zero is 'Dr'."""
    if signed >= 0:
        return signed, "Dr"
    return -signed, "Cr"


# ---------------------------------------------------------------------
# Exclusion counters (informational)
# ---------------------------------------------------------------------


def _count_excluded(
    db: Session, *, company_id: UUID, as_of_date: date
) -> tuple[int, int]:
    """Return (optional_count, cancelled_count) excluded by R1/R2."""
    optional_count = (
        db.query(func.count(Voucher.id))
        .filter(
            Voucher.company_id == company_id,
            Voucher.date <= as_of_date,
            Voucher.is_optional_in_tally.is_(True),
            Voucher.approved_to_regular_at.is_(None),
            Voucher.status != VoucherStatus.cancelled,
        )
        .scalar()
        or 0
    )
    cancelled_count = (
        db.query(func.count(Voucher.id))
        .filter(
            Voucher.company_id == company_id,
            Voucher.date <= as_of_date,
            Voucher.status == VoucherStatus.cancelled,
        )
        .scalar()
        or 0
    )
    return int(optional_count), int(cancelled_count)


# ---------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------


def compute_trial_balance(
    db: Session,
    *,
    company_id: UUID,
    as_of_date: date,
    group_filter: frozenset[str] | None = None,
) -> TrialBalanceResult:
    """Compute the trial balance for `company_id` as of `as_of_date`.

    If `group_filter` is provided, only ledgers whose `group_name`
    (normalized) is in the set are returned — used by the Outstanding
    report and by Balance Sheet sectioning.
    """
    movements = _period_movement_subquery(
        company_id=company_id, as_of_date=as_of_date
    )

    q = (
        db.query(
            Ledger.id,
            Ledger.name,
            Ledger.group_name,
            Ledger.opening_balance,
            Ledger.balance_type,
            func.coalesce(movements.c.total_dr, Decimal("0")).label("period_dr"),
            func.coalesce(movements.c.total_cr, Decimal("0")).label("period_cr"),
        )
        .outerjoin(movements, movements.c.ledger_id == Ledger.id)
        .filter(
            Ledger.company_id == company_id,
            Ledger.is_active.is_(True),
        )
    )

    rows: list[TrialBalanceRow] = []
    total_dr = Decimal("0")
    total_cr = Decimal("0")

    for r in q.all():
        if group_filter is not None:
            normalized = (r.group_name or "").strip().lower()
            if normalized not in group_filter:
                continue

        period_dr = Decimal(r.period_dr) if r.period_dr is not None else Decimal("0")
        period_cr = Decimal(r.period_cr) if r.period_cr is not None else Decimal("0")
        signed = _ledger_balance_signed(
            r.opening_balance, r.balance_type, period_dr, period_cr
        )
        # Skip rows with zero closing balance AND zero activity, to keep
        # the trial balance focused on meaningful lines. Tally hides
        # zero-balance ledgers by default in its trial balance view.
        if (
            signed == 0
            and period_dr == 0
            and period_cr == 0
            and r.opening_balance == 0
        ):
            continue

        closing_value, closing_type = _signed_to_pair(signed)
        opening_value, opening_type = _signed_to_pair(
            r.opening_balance
            if r.balance_type is BalanceType.Dr
            else -r.opening_balance
        )

        rows.append(
            TrialBalanceRow(
                ledger_id=r.id,
                ledger_name=r.name,
                group_name=r.group_name,
                opening_balance=opening_value,
                opening_balance_type=opening_type,
                period_dr=period_dr,
                period_cr=period_cr,
                closing_balance=closing_value,
                closing_balance_type=closing_type,
            )
        )
        if closing_type == "Dr":
            total_dr += closing_value
        else:
            total_cr += closing_value

    optional_count, cancelled_count = _count_excluded(
        db, company_id=company_id, as_of_date=as_of_date
    )

    rows.sort(key=lambda r: (r.group_name or "", r.ledger_name))

    return TrialBalanceResult(
        as_of_date=as_of_date,
        company_id=company_id,
        rows=rows,
        total_dr=total_dr,
        total_cr=total_cr,
        optional_excluded_count=optional_count,
        cancelled_excluded_count=cancelled_count,
    )
