"""Ingestions endpoints (Phase 1 wedge: invoice-scan upload + status)."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_active_company, get_current_user, get_scoped_session
from app.api.v1.auth import _user_audit_emitter
from app.core.exceptions import (
    IdempotencyKeyInvalid,
    IdempotencyKeyRequired,
    IngestionFileTooLarge,
    UnsupportedIngestionFile,
)
from app.core.idempotency import is_valid_key
from app.models.company import Company
from app.models.user import User
from app.schemas.ingestion import (
    IngestionDiscardRequest,
    IngestionLinkVoucherRequest,
    IngestionListItem,
    IngestionListResponse,
    IngestionOut,
    IngestionSourceStr,
)
from app.services.extraction.extraction_pipeline import enqueue_extraction
from app.services.ingestion_service import (
    ALLOWED_CONTENT_TYPES,
    MAX_CONTENT_SIZE,
    IngestionService,
)

router = APIRouter(prefix="/ingestions", tags=["ingestions"])
logger = logging.getLogger("app.api.v1.ingestions")


def _to_out(ing) -> IngestionOut:  # type: ignore[no-untyped-def]
    return IngestionOut(
        id=ing.id,
        company_id=ing.company_id,
        source=ing.source.value,
        status=ing.status.value,
        content_type=ing.content_type,
        content_size=ing.content_size,
        received_at=ing.received_at,
        extracted_at=ing.extracted_at,
        failed_at=ing.failed_at,
        failure_reason=ing.failure_reason,
        posted_voucher_id=ing.posted_voucher_id,
        parsed_data=ing.parsed_data,
        extraction_confidence=ing.extraction_confidence,
        extraction_flags=ing.extraction_flags,
        created_at=ing.created_at,
    )


@router.post(
    "/", status_code=status.HTTP_202_ACCEPTED, response_model=IngestionOut
)
async def create_ingestion(
    request: Request,
    file: Annotated[UploadFile, File()],
    source: Annotated[IngestionSourceStr, Form()] = "photo",
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> IngestionOut:
    # A multipart upload can't go through the generic header+body-hash
    # IdempotencyHandler (vouchers.py's pattern): FastAPI's own form
    # parser drains the request stream to resolve `file`/`source`
    # before the handler runs, so a later `request.body()` call raises
    # "Stream consumed". Dedup instead on the table's own
    # uq_ingestions_company_idempotency constraint -- a second request
    # with the same key hits the unique violation and we return the
    # row that already exists rather than creating a second one.
    if not idempotency_key:
        raise IdempotencyKeyRequired(
            "Idempotency-Key header is required for this endpoint."
        )
    if not is_valid_key(idempotency_key):
        raise IdempotencyKeyInvalid(
            "Idempotency-Key must be 8-255 ASCII printable characters "
            "with no whitespace."
        )

    content_type = file.content_type or ""
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedIngestionFile(
            f"unsupported content type: {content_type!r}",
            details={"allowed": sorted(ALLOWED_CONTENT_TYPES)},
        )
    content = await file.read()
    if len(content) > MAX_CONTENT_SIZE:
        raise IngestionFileTooLarge(
            f"file is {len(content)} bytes, max is {MAX_CONTENT_SIZE}"
        )

    audit = _user_audit_emitter(request, db, user, company=company)
    service = IngestionService(db, audit, company_id=company.id)
    try:
        ingestion = service.create(
            source=source,
            content_type=content_type,
            content=content,
            idempotency_key=idempotency_key,
            created_by=user.id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = service.get_by_idempotency_key(idempotency_key)
        if existing is None:
            raise  # some other integrity error -- don't swallow it
        return _to_out(existing)

    db.refresh(ingestion)
    try:
        enqueue_extraction(ingestion_id=ingestion.id, company_id=company.id)
    except Exception:
        logger.exception(
            "failed to enqueue extraction for ingestion %s", ingestion.id
        )
    return _to_out(ingestion)


@router.get("/", response_model=IngestionListResponse)
def list_ingestions(
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> IngestionListResponse:
    service = IngestionService(db, audit=None, company_id=company.id)  # type: ignore[arg-type]
    rows, total = service.list(status_filter=status_filter, limit=limit)
    return IngestionListResponse(
        items=[
            IngestionListItem(
                id=ing.id,
                source=ing.source.value,
                status=ing.status.value,
                content_type=ing.content_type,
                extraction_confidence=ing.extraction_confidence,
                extraction_flags=ing.extraction_flags,
                received_at=ing.received_at,
            )
            for ing in rows
        ],
        meta={"next_cursor": None, "total": total},
    )


@router.get("/{ingestion_id}", response_model=IngestionOut)
def get_ingestion(
    ingestion_id: UUID,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> IngestionOut:
    service = IngestionService(db, audit=None, company_id=company.id)  # type: ignore[arg-type]
    return _to_out(service.get(ingestion_id))


@router.post("/{ingestion_id}/discard", response_model=IngestionOut)
def discard_ingestion(
    ingestion_id: UUID,
    data: IngestionDiscardRequest,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> IngestionOut:
    """Mobile review queue: dismiss an ingestion that needs no voucher
    (duplicate, junk scan, handled elsewhere)."""
    audit = _user_audit_emitter(request, db, user, company=company)
    service = IngestionService(db, audit, company_id=company.id)
    ingestion = service.discard(ingestion_id, reason=data.reason)
    db.commit()
    db.refresh(ingestion)
    return _to_out(ingestion)


@router.post("/{ingestion_id}/link-voucher", response_model=IngestionOut)
def link_ingestion_voucher(
    ingestion_id: UUID,
    data: IngestionLinkVoucherRequest,
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_scoped_session),
) -> IngestionOut:
    """Mobile review queue: record that `voucher_id` (created by hand
    through the normal voucher-create flow) resolves this ingestion."""
    from app.services.voucher_service import VoucherService

    # Raises voucher_not_found (404) if it doesn't exist or belongs to
    # a different company -- same tenancy guarantee as every other
    # cross-entity reference in this API.
    VoucherService(db, audit=None, company_id=company.id).get(  # type: ignore[arg-type]
        data.voucher_id
    )

    audit = _user_audit_emitter(request, db, user, company=company)
    service = IngestionService(db, audit, company_id=company.id)
    ingestion = service.link_voucher(ingestion_id, voucher_id=data.voucher_id)
    db.commit()
    db.refresh(ingestion)
    return _to_out(ingestion)
