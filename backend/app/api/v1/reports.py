"""Reports endpoints (P0.38).

Four read-only endpoints over the same data per docs/REPORTS.md:

  GET /api/v1/reports/trial-balance
  GET /api/v1/reports/profit-loss
  GET /api/v1/reports/balance-sheet
  GET /api/v1/reports/outstanding?type=receivables|payables

Any membership role can read reports (per R9). No idempotency, no
audit emission — reads only. Computation lives in
`app/services/reporting/`.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_scoped_session,
)
from app.core.exceptions import DomainException
from app.models.company import Company
from app.models.user import User
from app.schemas.reports import (
    BalanceSheetResponse,
    BSEquation,
    BSGroup,
    BSLine,
    BSPnL,
    BSSection,
    OutstandingItem,
    OutstandingResponse,
    PnLLedger,
    PnLNet,
    PnLSection,
    ProfitLossResponse,
    TrialBalanceExclusions,
    TrialBalanceLedger,
    TrialBalanceResponse,
    TrialBalanceTotals,
)
from app.services.reporting.balance_sheet import compute_balance_sheet
from app.services.reporting.outstanding import compute_outstanding
from app.services.reporting.profit_loss import (
    compute_profit_loss,
    fiscal_year_start,
)
from app.services.reporting.trial_balance import compute_trial_balance

router = APIRouter(prefix="/reports", tags=["reports"])


class BalanceSheetUnbalanced(DomainException):
    """Asset side != Liabilities + Equity. Indicates data corruption."""

    status_code = 500
    code = "balance_sheet_unbalanced"


# ---------------------------------------------------------------------
# GET /reports/trial-balance
# ---------------------------------------------------------------------


@router.get("/trial-balance", response_model=TrialBalanceResponse)
def trial_balance(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    as_of_date: _date | None = Query(default=None),
) -> TrialBalanceResponse:
    target = as_of_date or _date.today()
    result = compute_trial_balance(
        db, company_id=company.id, as_of_date=target
    )
    return TrialBalanceResponse(
        as_of_date=result.as_of_date,
        company_id=result.company_id,
        ledgers=[
            TrialBalanceLedger(
                ledger_id=r.ledger_id,
                ledger_name=r.ledger_name,
                group_name=r.group_name,
                opening_balance=r.opening_balance,
                opening_balance_type=r.opening_balance_type,
                period_dr=r.period_dr,
                period_cr=r.period_cr,
                closing_balance=r.closing_balance,
                closing_balance_type=r.closing_balance_type,
            )
            for r in result.rows
        ],
        totals=TrialBalanceTotals(
            total_dr=result.total_dr,
            total_cr=result.total_cr,
            in_balance=result.in_balance,
        ),
        exclusions=TrialBalanceExclusions(
            optional_vouchers_excluded_count=result.optional_excluded_count,
            cancelled_vouchers_excluded_count=result.cancelled_excluded_count,
        ),
    )


# ---------------------------------------------------------------------
# GET /reports/profit-loss
# ---------------------------------------------------------------------


@router.get("/profit-loss", response_model=ProfitLossResponse)
def profit_loss(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    from_date: _date | None = Query(default=None),
    to_date: _date | None = Query(default=None),
) -> ProfitLossResponse:
    end = to_date or _date.today()
    start = from_date or fiscal_year_start(end)
    result = compute_profit_loss(
        db, company_id=company.id, from_date=start, to_date=end
    )
    return ProfitLossResponse(
        from_date=result.from_date,
        to_date=result.to_date,
        income=PnLSection(
            ledgers=[
                PnLLedger(
                    ledger_id=line.ledger_id,
                    ledger_name=line.ledger_name,
                    amount=line.amount,
                )
                for line in result.income.ledgers
            ],
            total=result.income.total,
        ),
        expense=PnLSection(
            ledgers=[
                PnLLedger(
                    ledger_id=line.ledger_id,
                    ledger_name=line.ledger_name,
                    amount=line.amount,
                )
                for line in result.expense.ledgers
            ],
            total=result.expense.total,
        ),
        net=PnLNet(value=result.net_value, type=result.net_type),
    )


# ---------------------------------------------------------------------
# GET /reports/balance-sheet
# ---------------------------------------------------------------------


@router.get("/balance-sheet", response_model=BalanceSheetResponse)
def balance_sheet(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    as_of_date: _date | None = Query(default=None),
) -> BalanceSheetResponse:
    target = as_of_date or _date.today()
    result = compute_balance_sheet(
        db, company_id=company.id, as_of_date=target
    )
    if not result.in_balance:
        # Per REPORTS.md: a balance sheet that doesn't balance means the
        # data is inconsistent — surface 500 so on-call notices.
        raise BalanceSheetUnbalanced(
            "Balance sheet equation does not hold; data integrity alert."
        )

    signed_pnl = (
        result.current_period_pnl_value
        if result.current_period_pnl_type == "profit"
        else -result.current_period_pnl_value
    )
    return BalanceSheetResponse(
        as_of_date=result.as_of_date,
        assets=_section_to_schema(result.assets),
        liabilities=_section_to_schema(result.liabilities),
        current_period_profit_loss=BSPnL(
            value=result.current_period_pnl_value,
            type=result.current_period_pnl_type,
        ),
        equation=BSEquation(
            assets=result.assets.total,
            liabilities_plus_equity=result.liabilities.total + signed_pnl,
            in_balance=result.in_balance,
        ),
    )


def _section_to_schema(section) -> BSSection:  # type: ignore[no-untyped-def]
    return BSSection(
        groups=[
            BSGroup(
                group_name=g.group_name,
                ledgers=[
                    BSLine(
                        ledger_id=line.ledger_id,
                        ledger_name=line.ledger_name,
                        amount=line.amount,
                    )
                    for line in g.ledgers
                ],
                total=g.total,
            )
            for g in section.groups
        ],
        total=section.total,
    )


# ---------------------------------------------------------------------
# GET /reports/outstanding
# ---------------------------------------------------------------------


@router.get("/outstanding", response_model=OutstandingResponse)
def outstanding(
    type: Literal["receivables", "payables"] = Query(
        ..., description="receivables = Sundry Debtors; payables = Sundry Creditors"
    ),
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    as_of_date: _date | None = Query(default=None),
) -> OutstandingResponse:
    target = as_of_date or _date.today()
    result = compute_outstanding(
        db, company_id=company.id, as_of_date=target, type_=type
    )
    return OutstandingResponse(
        type=result.type,
        as_of_date=result.as_of_date,
        items=[
            OutstandingItem(
                ledger_id=i.ledger_id,
                ledger_name=i.ledger_name,
                ledger_gstin=i.ledger_gstin,
                balance=i.balance,
                balance_type=i.balance_type,
            )
            for i in result.items
        ],
        total=result.total,
        total_type=result.total_type,
    )
