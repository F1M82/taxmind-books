"""Vouchers endpoints (P0.18: create; P0.19: read, list, update, cancel)."""

from __future__ import annotations

from datetime import date as _date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_company,
    get_current_user,
    get_idempotency_handler,
    get_scoped_session,
)
from app.api.v1.auth import _user_audit_emitter
from app.core.idempotency import IdempotencyHandler
from app.models.company import Company
from app.models.user import User
from app.schemas.voucher import (
    VoucherCancelRequest,
    VoucherCreate,
    VoucherEntryOut,
    VoucherListItem,
    VoucherListResponse,
    VoucherOut,
    VoucherUpdate,
)
from app.services.voucher_service import VoucherService

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


def _to_out(v) -> VoucherOut:  # type: ignore[no-untyped-def]
    return VoucherOut(
        id=v.id,
        company_id=v.company_id,
        voucher_type=v.voucher_type.value
        if hasattr(v.voucher_type, "value")
        else str(v.voucher_type),
        voucher_number=v.voucher_number,
        date=v.date,
        narration=v.narration,
        reference=v.reference,
        total_amount=v.total_amount,
        status=v.status.value if hasattr(v.status, "value") else str(v.status),
        source=v.source,
        is_auto_posted=v.is_auto_posted,
        confidence_score=v.confidence_score,
        gst_applicable=v.gst_applicable,
        place_of_supply=v.place_of_supply,
        cgst=v.cgst,
        sgst=v.sgst,
        igst=v.igst,
        cess=v.cess,
        tds_applicable=v.tds_applicable,
        tds_amount=v.tds_amount,
        tds_section=v.tds_section,
        tally_posted_at=v.tally_posted_at,
        created_by=v.created_by,
        created_at=v.created_at,
        entries=[
            VoucherEntryOut(
                id=e.id,
                ledger_id=e.ledger_id,
                amount=e.amount,
                entry_type=e.entry_type.value
                if hasattr(e.entry_type, "value")
                else str(e.entry_type),
                line_number=e.line_number,
                narration=e.narration,
                gst_rate=e.gst_rate,
                cgst=e.cgst,
                sgst=e.sgst,
                igst=e.igst,
                tds_amount=e.tds_amount,
                tds_section=e.tds_section,
            )
            for e in sorted(v.entries, key=lambda e: e.line_number)
        ],
    )


@router.post(
    "/", status_code=status.HTTP_201_CREATED, response_model=VoucherOut
)
async def create_voucher(
    data: VoucherCreate,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    idem: IdempotencyHandler = Depends(get_idempotency_handler),
) -> Response | VoucherOut:
    replay = await idem.check(required=True)
    if replay is not None:
        return replay

    audit = _user_audit_emitter(request, db, user, company=company)
    service = VoucherService(db, audit, company_id=company.id)
    voucher = service.create(data, actor=user)
    db.commit()
    db.refresh(voucher)

    out = _to_out(voucher)
    idem.store_response(
        status_code=201, body=out.model_dump(mode="json")
    )
    db.commit()

    # Enqueue Tally posting (no-op when no connector is online; the
    # task itself retries via Celery backoff on ConnectorOffline).
    from uuid import uuid4

    from app.services.tally.voucher_dispatcher import enqueue_voucher_post

    try:
        enqueue_voucher_post(
            voucher_id=voucher.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
        )
    except Exception:  # noqa: BLE001 — broker outage must not 500 the create
        import logging

        logging.getLogger("app.api.v1.vouchers").exception(
            "failed to enqueue post_voucher_to_tally for %s", voucher.id
        )
    return out


# ---------------------------------------------------------------------
# List
# ---------------------------------------------------------------------


@router.get("/", response_model=VoucherListResponse)
def list_vouchers(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    voucher_type: str | None = None,
    date_from: _date | None = Query(default=None, alias="from"),
    date_to: _date | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
    ledger_id: UUID | None = None,
    source: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> VoucherListResponse:
    service = VoucherService(db, audit=None, company_id=company.id)  # type: ignore[arg-type]
    rows, total = service.list(
        voucher_type=voucher_type,
        date_from=date_from,
        date_to=date_to,
        status_filter=status_filter,
        ledger_id=ledger_id,
        source=source,
        limit=limit,
    )
    return VoucherListResponse(
        items=[
            VoucherListItem(
                id=v.id,
                voucher_type=v.voucher_type.value
                if hasattr(v.voucher_type, "value")
                else str(v.voucher_type),
                voucher_number=v.voucher_number,
                date=v.date,
                narration=v.narration,
                reference=v.reference,
                total_amount=v.total_amount,
                status=v.status.value
                if hasattr(v.status, "value")
                else str(v.status),
                source=v.source,
                gst_applicable=v.gst_applicable,
                tally_posted_at=v.tally_posted_at,
                created_at=v.created_at,
            )
            for v in rows
        ],
        meta={"next_cursor": None, "total": total},
    )


# ---------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------


@router.get("/{voucher_id}", response_model=VoucherOut)
def get_voucher(
    voucher_id: UUID,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> VoucherOut:
    service = VoucherService(db, audit=None, company_id=company.id)  # type: ignore[arg-type]
    voucher = service.get(voucher_id)
    return _to_out(voucher)


# ---------------------------------------------------------------------
# Update (narration / reference only)
# ---------------------------------------------------------------------


@router.patch("/{voucher_id}", response_model=VoucherOut)
async def update_voucher(
    voucher_id: UUID,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> VoucherOut:
    raw = await request.json()
    if not isinstance(raw, dict):
        raw = {}
    data = VoucherUpdate.model_validate(raw)
    raw_keys = set(raw.keys())
    audit = _user_audit_emitter(request, db, user, company=company)
    service = VoucherService(db, audit, company_id=company.id)
    voucher = service.update(voucher_id, data, raw_keys=raw_keys)
    db.commit()
    db.refresh(voucher)
    return _to_out(voucher)


# ---------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------


@router.post("/{voucher_id}/cancel", response_model=VoucherOut)
def cancel_voucher(
    voucher_id: UUID,
    data: VoucherCancelRequest,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> VoucherOut:
    audit = _user_audit_emitter(request, db, user, company=company)
    service = VoucherService(db, audit, company_id=company.id)
    voucher = service.cancel(voucher_id, reason=data.reason)
    db.commit()
    db.refresh(voucher)
    return _to_out(voucher)
