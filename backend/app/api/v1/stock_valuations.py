"""Stock valuation endpoints.

  GET  /api/v1/stock-valuations                 list (any role)
  PUT  /api/v1/stock-valuations/{period_from}   manual entry (owner/admin)

Pulling the value from Tally is ``POST /api/v1/connector/stock-valuation/
{company_id}`` (see connector.py). Design: docs/PHASE_3_CLOSING_STOCK_DESIGN.md.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_scoped_session,
    require_role,
)
from app.api.v1.auth import _user_audit_emitter
from app.models.company import Company, CompanyRole
from app.models.stock_valuation import StockValuation
from app.models.user import User
from app.schemas.stock_valuation import (
    StockValuationListResponse,
    StockValuationManualRequest,
    StockValuationOut,
)
from app.services.reporting.stock import fy_end, fy_start_year
from app.services.stock_valuation_service import (
    StockValuationService,
    list_valuations,
)

router = APIRouter(prefix="/stock-valuations", tags=["stock-valuations"])


def to_out(v: StockValuation) -> StockValuationOut:
    year = fy_start_year(v.period_from)
    return StockValuationOut(
        id=v.id,
        label=f"FY {year}-{str(year + 1)[-2:]}",
        period_from=v.period_from,
        period_to=v.period_to,
        opening_value=v.opening_value,
        closing_value=v.closing_value,
        source=v.source,
        item_count=v.item_count,
        negative_stock_items=v.negative_stock_items,
        captured_at=v.captured_at,
    )


@router.get("", response_model=StockValuationListResponse)
def list_stock_valuations(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> StockValuationListResponse:
    """Recorded valuations, newest financial year first."""
    return StockValuationListResponse(
        items=[to_out(v) for v in list_valuations(db, company.id)]
    )


@router.put("/{period_from}", response_model=StockValuationOut)
def put_stock_valuation(
    period_from: date,
    body: StockValuationManualRequest,
    request: Request,
    company: Company = Depends(
        require_role(CompanyRole.owner, CompanyRole.admin)
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> StockValuationOut:
    """Enter (or replace) the stock value for one financial year by hand.

    ``period_from`` must be a 1 April; the year end is derived. For years
    the connector cannot read (Tally's period was never set to that year).
    Same rules as a Tally pull: single financial year, and the first year's
    opening must equal the Stock-in-Hand ledger balance.
    """
    audit = _user_audit_emitter(request, db, user, company=company)
    service = StockValuationService(db, audit, company_id=company.id)
    row = service.record(
        period_from=period_from,
        period_to=fy_end(fy_start_year(period_from)),
        opening_value=body.opening_value,
        closing_value=body.closing_value,
        source="manual",
        captured_by=user.id,
    )
    db.commit()
    db.refresh(row)
    return to_out(row)
