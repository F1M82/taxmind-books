"""Outstanding receivables / payables per docs/REPORTS.md §"Outstanding".

Reuses `compute_trial_balance` with a group filter — receivables sums
all Sundry Debtors closing balances, payables sums all Sundry Creditors.
The classification matches what Tally calls "Bills Receivable" /
"Bills Payable" reports.

The aged-outstanding breakdown (with FIFO matching against payments) is
a Phase-1 analytic; this Phase-0 endpoint is the flat per-party total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.reporting.trial_balance import (
    TrialBalanceRow,
    compute_trial_balance,
)

OutstandingType = Literal["receivables", "payables"]


@dataclass
class OutstandingItem:
    ledger_id: UUID
    ledger_name: str
    ledger_gstin: str | None
    balance: Decimal
    balance_type: str


@dataclass
class OutstandingResult:
    type: OutstandingType
    as_of_date: date
    items: list[OutstandingItem]
    total: Decimal
    total_type: str


_RECEIVABLES_GROUPS = frozenset({"sundry debtors"})
_PAYABLES_GROUPS = frozenset({"sundry creditors"})


def compute_outstanding(
    db: Session,
    *,
    company_id: UUID,
    as_of_date: date,
    type_: OutstandingType,
) -> OutstandingResult:
    """Sum of party-ledger closing balances by Sundry group.

    The signed-total convention: receivables totals are reported with
    `total_type='Dr'` (parties owe us), payables with `total_type='Cr'`
    (we owe them). When the net flips sign (e.g., a customer is in
    advance), `total_type` follows the dominant direction.
    """
    group_filter = (
        _RECEIVABLES_GROUPS if type_ == "receivables" else _PAYABLES_GROUPS
    )
    tb = compute_trial_balance(
        db,
        company_id=company_id,
        as_of_date=as_of_date,
        group_filter=group_filter,
    )

    # Pull GSTINs in one query so we don't N+1 the ledger lookup.
    from app.models.ledger import Ledger

    gstins: dict[UUID, str | None] = {}
    for lid, gstin in (
        db.query(Ledger.id, Ledger.gstin)
        .filter(Ledger.id.in_([r.ledger_id for r in tb.rows]))
        .all()
    ):
        gstins[lid] = gstin

    items: list[OutstandingItem] = []
    signed_total = Decimal("0")
    for r in tb.rows:
        items.append(
            OutstandingItem(
                ledger_id=r.ledger_id,
                ledger_name=r.ledger_name,
                ledger_gstin=gstins.get(r.ledger_id),
                balance=r.closing_balance,
                balance_type=r.closing_balance_type,
            )
        )
        signed_total += _signed(r)

    total_value = abs(signed_total)
    total_type = "Dr" if signed_total >= 0 else "Cr"
    return OutstandingResult(
        type=type_,
        as_of_date=as_of_date,
        items=items,
        total=total_value,
        total_type=total_type,
    )


def _signed(row: TrialBalanceRow) -> Decimal:
    return row.closing_balance if row.closing_balance_type == "Dr" else -row.closing_balance
