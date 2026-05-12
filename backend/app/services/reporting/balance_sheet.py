"""Balance sheet per docs/REPORTS.md §"Balance Sheet".

Snapshot of assets, liabilities, and equity as of a date. Equity is
folded in as the current-FY P&L: profit increases liabilities side,
loss decreases it (mirroring Tally's "Difference in Opening Balances"
behavior when a P&L is in flight).

The validation property is that `assets == liabilities + current_pnl`
to the rupee. If it doesn't, the data is inconsistent and the API
layer surfaces a 500 with an alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.reporting.profit_loss import (
    compute_profit_loss,
    fiscal_year_start,
)
from app.services.reporting.tally_groups import ASSET_GROUPS, LIABILITY_GROUPS
from app.services.reporting.trial_balance import (
    TrialBalanceRow,
    compute_trial_balance,
)


@dataclass
class BSLine:
    ledger_id: UUID
    ledger_name: str
    amount: Decimal  # always positive; sign carried by section


@dataclass
class BSGroup:
    group_name: str
    ledgers: list[BSLine] = field(default_factory=list)
    total: Decimal = Decimal("0")


@dataclass
class BSSection:
    groups: list[BSGroup] = field(default_factory=list)
    total: Decimal = Decimal("0")


@dataclass
class BalanceSheetResult:
    as_of_date: date
    assets: BSSection
    liabilities: BSSection
    current_period_pnl_value: Decimal
    current_period_pnl_type: Literal["profit", "loss"]
    in_balance: bool


def _section_from_rows(
    rows: list[TrialBalanceRow], *, sign_for_section: str
) -> BSSection:
    """Bucket rows by group_name.

    `sign_for_section` is 'Dr' for assets, 'Cr' for liabilities. A row
    whose closing_balance_type matches the section sign contributes
    positively; mismatched rows (a customer in advance shows up as Cr
    in receivables, etc.) contribute negatively.
    """
    by_group: dict[str, BSGroup] = {}
    section_total = Decimal("0")
    for r in rows:
        grp_name = r.group_name or "Unclassified"
        bsg = by_group.setdefault(grp_name, BSGroup(group_name=grp_name))
        contribution = (
            r.closing_balance
            if r.closing_balance_type == sign_for_section
            else -r.closing_balance
        )
        bsg.ledgers.append(
            BSLine(
                ledger_id=r.ledger_id,
                ledger_name=r.ledger_name,
                amount=contribution,
            )
        )
        bsg.total += contribution
        section_total += contribution

    section = BSSection(total=section_total)
    section.groups = sorted(by_group.values(), key=lambda g: g.group_name)
    for g in section.groups:
        g.ledgers.sort(key=lambda x: x.ledger_name)
    return section


def compute_balance_sheet(
    db: Session,
    *,
    company_id: UUID,
    as_of_date: date,
) -> BalanceSheetResult:
    """Compute the balance sheet as of `as_of_date`."""
    asset_tb = compute_trial_balance(
        db,
        company_id=company_id,
        as_of_date=as_of_date,
        group_filter=_normalize_groups(ASSET_GROUPS),
    )
    liability_tb = compute_trial_balance(
        db,
        company_id=company_id,
        as_of_date=as_of_date,
        group_filter=_normalize_groups(LIABILITY_GROUPS),
    )

    assets = _section_from_rows(asset_tb.rows, sign_for_section="Dr")
    liabilities = _section_from_rows(liability_tb.rows, sign_for_section="Cr")

    pnl = compute_profit_loss(
        db,
        company_id=company_id,
        from_date=fiscal_year_start(as_of_date),
        to_date=as_of_date,
    )
    # Profit increases liabilities (owners' equity); loss decreases.
    signed_pnl = pnl.net_value if pnl.net_type == "profit" else -pnl.net_value

    in_balance = assets.total == liabilities.total + signed_pnl
    return BalanceSheetResult(
        as_of_date=as_of_date,
        assets=assets,
        liabilities=liabilities,
        current_period_pnl_value=pnl.net_value,
        current_period_pnl_type=pnl.net_type,
        in_balance=in_balance,
    )


def _normalize_groups(groups: frozenset[str]) -> frozenset[str]:
    # `compute_trial_balance` lowercases the ledger's group_name before
    # comparing; the constant sets already store lowercased values, so
    # this is the identity function — kept for callsite clarity.
    return groups
