"""Dashboard endpoint (P0.40) — `GET /api/v1/dashboard/home`."""

from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_scoped_session,
)
from app.models.company import Company
from app.models.user import User
from app.schemas.dashboard import (
    DashboardAlert,
    DashboardConnector,
    DashboardFinancialsResponse,
    DashboardGstLiability,
    DashboardHomeResponse,
    DashboardMonthMetrics,
    DashboardNetProfit,
    DashboardOutstanding,
    DashboardTodayMetrics,
)
from app.services.dashboard_service import build_dashboard
from app.services.reporting.profit_loss import (
    compute_dashboard_financials,
    fiscal_year_start,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/home", response_model=DashboardHomeResponse)
def dashboard_home(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> DashboardHomeResponse:
    data = build_dashboard(db, company=company)
    return DashboardHomeResponse(
        as_of=data.as_of,
        company_name=data.company_name,
        connector=DashboardConnector(
            connected=data.connector.connected,
            tally_running=data.connector.tally_running,
            last_seen_seconds_ago=data.connector.last_seen_seconds_ago,
        ),
        today=DashboardTodayMetrics(
            vouchers_created=data.today.vouchers_created,
            vouchers_pending_approval=data.today.vouchers_pending_approval,
            cash_in=data.today.cash_in,
            cash_out=data.today.cash_out,
        ),
        this_month=DashboardMonthMetrics(
            cash_in=data.this_month.cash_in,
            cash_out=data.this_month.cash_out,
            vouchers_created=data.this_month.vouchers_created,
            vouchers_pending_approval=data.this_month.vouchers_pending_approval,
        ),
        outstanding=DashboardOutstanding(
            receivables_total=data.outstanding.receivables_total,
            payables_total=data.outstanding.payables_total,
        ),
        gst_liability_indicative=DashboardGstLiability(
            month_to_date=data.gst_liability_month_to_date
        ),
        alerts=[
            DashboardAlert(
                kind=a.kind,
                severity=a.severity,
                message=a.message,
                since=a.since,
            )
            for a in data.alerts
        ],
    )


@router.get("/financials", response_model=DashboardFinancialsResponse)
def dashboard_financials(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    from_date: _date | None = Query(default=None, alias="from"),
    to_date: _date | None = Query(default=None, alias="to"),
) -> DashboardFinancialsResponse:
    """Headline financials (Sales / Purchase / Expenses / Net Profit) over
    a selectable period. Defaults to the current Indian financial year
    (April 1 → today) when no dates are supplied."""
    end = to_date or _date.today()
    start = from_date or fiscal_year_start(end)
    result = compute_dashboard_financials(
        db, company_id=company.id, from_date=start, to_date=end
    )
    return DashboardFinancialsResponse(
        from_date=result.from_date,
        to_date=result.to_date,
        sales=result.sales,
        purchase=result.purchase,
        expenses=result.expenses,
        net_profit=DashboardNetProfit(
            value=result.net_profit_value,
            type=result.net_profit_type,
        ),
    )
