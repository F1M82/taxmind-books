"""Orchestrates one ingestion through the extraction pipeline.

storage.read -> image_prep.prepare_images -> invoice_extractor.extract
-> validator.validate -> party_matcher.match_party -> Ingestion row
updated. Per docs/EXTRACTION_CONTRACT.md end-to-end flow (steps 2-9);
see CLAUDE.md "Phase 1 scope" for why it stops one step short of
auto-creating the Voucher (step 9) -- that needs a product decision
this pipeline doesn't have an answer for yet (which ledger is the
non-party side of a Purchase/Sales entry when nothing in the
architecture docs defines a default/suspense ledger).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.audit import AuditContext, AuditEmitter
from app.core.exceptions import (
    LedgerNotFound,
    LedgerNotSyncedToTally,
    ValidationFailed,
    VoucherEntriesUnbalanced,
    VoucherTypeRuleViolation,
)
from app.models.company import Company
from app.models.ingestion import Ingestion, IngestionStatus
from app.models.user import User
from app.schemas.voucher import VoucherCreate, VoucherEntryCreate
from app.services.extraction import image_prep, party_matcher, storage
from app.services.extraction.extraction_schema import ExtractedInvoice
from app.services.extraction.invoice_extractor import (
    ExtractionHardFailure,
    ExtractionMalformedOutput,
    extract_invoice,
)
from app.services.extraction.validator import NotAnInvoice, ValidationResult, validate
from app.services.voucher_service import VoucherService

logger = logging.getLogger("app.services.extraction.extraction_pipeline")

LOW_CONFIDENCE_THRESHOLD = 0.40

# Errors that mean "this invoice doesn't fit the minimal v1 auto-voucher
# shape" -- never a pipeline bug. Caught so a weird invoice falls back to
# the pre-existing behavior (ingestion stays `matched`/`review`,
# posted_voucher_id stays NULL, a human posts it by hand) instead of
# failing the whole extraction.
_AUTO_POST_SKIP_ERRORS = (
    VoucherTypeRuleViolation,
    LedgerNotFound,
    LedgerNotSyncedToTally,
    VoucherEntriesUnbalanced,
    ValidationFailed,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _try_auto_post(
    db: Session,
    *,
    ingestion: Ingestion,
    company: Company,
    extracted: ExtractedInvoice,
    result: ValidationResult,
    party_ledger_id: UUID,
) -> UUID | None:
    """Build and create the 2-line Purchase voucher for a clean
    extraction, per the per-company default-ledger decision (CLAUDE.md
    "Phase 1 wedge"). Returns the new voucher's id, or None if this
    invoice doesn't fit the minimal v1 shape -- never raises; the caller
    leaves the ingestion exactly as before this feature existed.
    """
    if result.suggested_voucher_type != "Purchase":
        return None  # self-invoice (Sales) case: no default sales ledger yet
    if company.default_purchase_ledger_id is None:
        return None  # owner/admin hasn't configured one (PATCH /companies/{id})
    if extracted.total_amount is None or extracted.total_amount <= 0:
        return None

    gst_applicable = bool(
        (extracted.cgst or Decimal("0"))
        + (extracted.sgst or Decimal("0"))
        + (extracted.igst or Decimal("0"))
    )
    place_of_supply = result.vendor_state_code if gst_applicable else None
    if gst_applicable and place_of_supply is None:
        return None  # VoucherCreate requires it; we have no state code to offer

    actor = db.query(User).filter(User.id == ingestion.created_by).first()
    if actor is None:
        return None  # no known uploader to attribute the voucher to

    audit = AuditEmitter(
        db,
        AuditContext(
            company=company,
            user=actor,
            ip_address=None,
            user_agent="extraction-pipeline/1.0",
            request_id=uuid4(),
            source="worker",
        ),
    )
    service = VoucherService(db, audit, company.id)
    try:
        data = VoucherCreate(
            voucher_type="Purchase",
            date=extracted.invoice_date or ingestion.received_at.date(),
            narration=(
                f"Auto-extracted from {ingestion.source.value} invoice"
                + (f" #{extracted.invoice_number}" if extracted.invoice_number else "")
            ),
            reference=extracted.invoice_number,
            total_amount=extracted.total_amount,
            entries=[
                VoucherEntryCreate(
                    ledger_id=company.default_purchase_ledger_id,
                    amount=extracted.total_amount,
                    entry_type="Dr",
                ),
                VoucherEntryCreate(
                    ledger_id=party_ledger_id,
                    amount=extracted.total_amount,
                    entry_type="Cr",
                ),
            ],
            gst_applicable=gst_applicable,
            place_of_supply=place_of_supply,
            cgst=extracted.cgst or Decimal("0"),
            sgst=extracted.sgst or Decimal("0"),
            igst=extracted.igst or Decimal("0"),
        )
        voucher = service.create(
            data,
            actor,
            source=ingestion.source.value,
            source_ingestion_id=ingestion.id,
            is_auto_posted=True,
            confidence_score=Decimal(str(round(result.confidence, 3))),
            force_optional=True,
        )
    except (*_AUTO_POST_SKIP_ERRORS, PydanticValidationError):
        logger.info(
            "ingestion %s: extraction matched but auto-voucher-construction "
            "skipped (invoice doesn't fit the minimal v1 shape)",
            ingestion.id,
            exc_info=True,
        )
        return None
    return voucher.id


def _fail(
    db: Session, ingestion: Ingestion, *, reason: str
) -> None:
    ingestion.status = IngestionStatus.failed
    ingestion.failure_reason = reason
    ingestion.failed_at = _now()
    db.commit()


def run_extraction(
    db: Session, *, ingestion_id: UUID, company_id: UUID
) -> None:
    """Run one extraction attempt. Raises `ExtractionHardFailure` for a
    transport-level LLM failure so the caller's retry policy (Celery
    backoff, or the eager-mode caller's own handling) can decide what
    to do next -- every other outcome is terminal and committed here.
    """
    ingestion = (
        db.query(Ingestion)
        .filter(Ingestion.id == ingestion_id, Ingestion.company_id == company_id)
        .first()
    )
    if ingestion is None:
        raise ValueError(
            f"ingestion {ingestion_id} not found in company {company_id}"
        )

    ingestion.status = IngestionStatus.extracting
    db.commit()

    if ingestion.s3_object_key is None:
        _fail(db, ingestion, reason="no_file_stored")
        return
    try:
        content = storage.read_bytes(ingestion.s3_object_key)
    except FileNotFoundError:
        _fail(db, ingestion, reason="file_not_found")
        return

    try:
        images, prep_flags = image_prep.prepare_images(
            content, ingestion.content_type or ""
        )
    except (image_prep.UnsupportedContentType, image_prep.CorruptFile) as exc:
        _fail(db, ingestion, reason=str(exc))
        return

    try:
        extracted, meta = extract_invoice(images)
    except ExtractionMalformedOutput:
        _fail(db, ingestion, reason="extraction_malformed_output")
        return
    except ExtractionHardFailure:
        raise

    company = db.query(Company).filter(Company.id == company_id).first()

    try:
        result = validate(
            extracted,
            active_company_gstin=company.gstin if company else None,
        )
    except NotAnInvoice:
        ingestion.status = IngestionStatus.discarded
        ingestion.extracted_at = _now()
        db.commit()
        return

    if extracted.extraction_confidence < LOW_CONFIDENCE_THRESHOLD:
        result.flags.append("low_extraction_confidence")
        result.needs_review = True

    party = party_matcher.match_party(
        db,
        company_id=company_id,
        vendor_gstin=result.vendor_gstin,
        vendor_pan=result.vendor_pan,
        vendor_name=extracted.vendor_name,
    )
    all_flags = sorted(set(result.flags + prep_flags + party.flags))

    parsed_data = extracted.model_dump(mode="json")
    parsed_data["vendor_gstin"] = result.vendor_gstin
    parsed_data["vendor_pan"] = result.vendor_pan
    parsed_data["vendor_state_code"] = result.vendor_state_code
    parsed_data["suggested_voucher_type"] = result.suggested_voucher_type
    parsed_data["suggested_party_ledger_id"] = (
        str(party.ledger_id) if party.ledger_id else None
    )
    parsed_data["_meta"] = meta

    ingestion.parsed_data = parsed_data
    ingestion.extraction_confidence = Decimal(str(round(result.confidence, 3)))
    ingestion.extraction_flags = all_flags
    ingestion.extracted_at = _now()
    is_matched = not (result.needs_review or party.ledger_id is None)
    ingestion.status = (
        IngestionStatus.matched if is_matched else IngestionStatus.review
    )

    if is_matched and company is not None:
        assert party.ledger_id is not None  # guaranteed by is_matched above
        voucher_id = _try_auto_post(
            db,
            ingestion=ingestion,
            company=company,
            extracted=extracted,
            result=result,
            party_ledger_id=party.ledger_id,
        )
        if voucher_id is not None:
            ingestion.posted_voucher_id = voucher_id
            ingestion.status = IngestionStatus.posted

    db.commit()


# ---------------------------------------------------------------------
# Enqueue hook -- same eager/Celery split as voucher_dispatcher's
# enqueue_voucher_post (see its docstring for why: Phase 0 runs single-
# process with CELERY_TASK_ALWAYS_EAGER=1).
# ---------------------------------------------------------------------


def enqueue_extraction(*, ingestion_id: UUID, company_id: UUID) -> None:
    """Fire-and-forget: schedule `run_extraction` without blocking the
    HTTP response that just created the ingestion row."""
    settings = get_settings()

    if settings.TAXMIND_SKIP_TALLY_DISPATCH:
        # Reuses the existing "don't reach out to external services
        # during tests" switch rather than adding a second flag -- same
        # choice auto-sync-on-connect made (see CLAUDE.md).
        return

    if settings.CELERY_TASK_ALWAYS_EAGER:
        _enqueue_in_process(ingestion_id=ingestion_id, company_id=company_id)
        return

    from app.workers.extraction_tasks import extract_invoice_task

    extract_invoice_task.delay(
        ingestion_id=str(ingestion_id), company_id=str(company_id)
    )


def _enqueue_in_process(*, ingestion_id: UUID, company_id: UUID) -> None:
    """Schedule the extraction on the current asyncio event loop.

    The Gemini call and image prep are blocking, so the coroutine runs
    them in a thread (`asyncio.to_thread`) rather than on the event
    loop itself -- otherwise one in-flight extraction would stall every
    other request this process is serving.
    """
    import asyncio

    from app.core.database import SessionLocal

    async def _drive() -> None:
        db = SessionLocal()
        try:
            await asyncio.to_thread(
                run_extraction,
                db,
                ingestion_id=ingestion_id,
                company_id=company_id,
            )
        except ExtractionHardFailure as exc:
            # No Celery retry in eager mode (same trade-off
            # voucher_dispatcher._enqueue_in_process documents). The
            # ingestion is left in `extracting`; a manual re-trigger
            # endpoint is a follow-up, not built in this pass.
            db.rollback()
            logger.warning(
                "in-process extraction failed for ingestion %s: %s "
                "(no autoretry in eager mode)",
                ingestion_id,
                exc,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "in-process extraction failed for ingestion %s", ingestion_id
            )
        finally:
            db.close()

    loop = asyncio.get_running_loop()
    loop.create_task(_drive())
