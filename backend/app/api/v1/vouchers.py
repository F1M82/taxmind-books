"""Vouchers endpoints (P0.18: create only). Read/list/update/cancel: P0.19."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
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
from app.schemas.voucher import VoucherCreate, VoucherEntryOut, VoucherOut
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
    return out
