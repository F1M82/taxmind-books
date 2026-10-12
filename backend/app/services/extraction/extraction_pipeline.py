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
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.company import Company
from app.models.ingestion import Ingestion, IngestionStatus
from app.services.extraction import image_prep, party_matcher, storage
from app.services.extraction.invoice_extractor import (
    ExtractionHardFailure,
    ExtractionMalformedOutput,
    extract_invoice,
)
from app.services.extraction.validator import NotAnInvoice, validate

logger = logging.getLogger("app.services.extraction.extraction_pipeline")

LOW_CONFIDENCE_THRESHOLD = 0.40


def _now() -> datetime:
    return datetime.now(UTC)


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
    ingestion.status = (
        IngestionStatus.review
        if (result.needs_review or party.ledger_id is None)
        else IngestionStatus.matched
    )
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
