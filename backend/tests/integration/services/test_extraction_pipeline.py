"""Integration tests for run_extraction: real DB + storage + image_prep,
mocked LLM call only (never hits a real Gemini API)."""

from __future__ import annotations

import io
from uuid import uuid4

import pytest
from app.models.ingestion import Ingestion, IngestionSource, IngestionStatus
from app.models.ledger import Ledger
from app.models.voucher import Voucher
from app.services.extraction import extraction_pipeline, storage
from app.services.extraction.extraction_schema import ExtractedInvoice
from app.services.extraction.invoice_extractor import (
    ExtractionHardFailure,
    ExtractionMalformedOutput,
)
from PIL import Image
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company, make_user


def _jpeg_bytes() -> bytes:
    img = Image.new("RGB", (80, 60), color="green")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _seed_ingestion(db: Session, company, *, created_by=None) -> Ingestion:  # type: ignore[no-untyped-def]
    key = f"{company.id}/{uuid4()}/original.jpg"
    storage.save_bytes(key, _jpeg_bytes())
    ing = Ingestion(
        company_id=company.id,
        source=IngestionSource.photo,
        s3_object_key=key,
        content_type="image/jpeg",
        content_size=100,
        created_by=created_by.id if created_by else None,
    )
    db.add(ing)
    db.commit()
    return ing


def _patch_extract(monkeypatch, invoice: ExtractedInvoice):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        extraction_pipeline,
        "extract_invoice",
        lambda images: (invoice, {"model": "gemini-2.0-flash"}),
    )


def test_matched_party_lands_in_matched_status(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = make_company(db_session)
    ledger = Ledger(
        company_id=company.id,
        name="Sharma Traders",
        name_normalized="sharma traders",
        group_name="Sundry Creditors",
        gstin="27AAACR5055K1Z8",
    )
    db_session.add(ledger)
    db_session.commit()
    ing = _seed_ingestion(db_session, company)

    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "Sharma Traders",
            "vendor_gstin": "27AAACR5055K1Z8",
            "total_amount": "11800.00",
            "subtotal": "10000.00",
            "cgst": "900.00",
            "sgst": "900.00",
            "extraction_confidence": 0.97,
            "is_likely_invoice": True,
        }
    )
    _patch_extract(monkeypatch, invoice)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.matched
    assert ing.parsed_data["suggested_party_ledger_id"] == str(ledger.id)
    assert "new_party" not in ing.extraction_flags
    assert ing.extraction_confidence > 0


def test_no_party_match_lands_in_review(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = make_company(db_session)
    ing = _seed_ingestion(db_session, company)

    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "Totally Unknown Vendor",
            "total_amount": "500.00",
            "extraction_confidence": 0.95,
            "is_likely_invoice": True,
        }
    )
    _patch_extract(monkeypatch, invoice)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.review
    assert "new_party" in ing.extraction_flags


def test_not_an_invoice_is_discarded(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = make_company(db_session)
    ing = _seed_ingestion(db_session, company)

    invoice = ExtractedInvoice.model_validate(
        {
            "total_amount": "0.00",
            "extraction_confidence": 0.1,
            "is_likely_invoice": False,
            "extraction_warnings": ["this is a selfie"],
        }
    )
    _patch_extract(monkeypatch, invoice)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.discarded
    assert ing.parsed_data is None


def test_malformed_output_marks_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = make_company(db_session)
    ing = _seed_ingestion(db_session, company)

    def _raise(images):  # type: ignore[no-untyped-def]
        raise ExtractionMalformedOutput("bad json")

    monkeypatch.setattr(extraction_pipeline, "extract_invoice", _raise)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.failed
    assert ing.failure_reason == "extraction_malformed_output"


def test_hard_failure_propagates_for_caller_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = make_company(db_session)
    ing = _seed_ingestion(db_session, company)

    def _raise(images):  # type: ignore[no-untyped-def]
        raise ExtractionHardFailure("network down")

    monkeypatch.setattr(extraction_pipeline, "extract_invoice", _raise)

    with pytest.raises(ExtractionHardFailure):
        extraction_pipeline.run_extraction(
            db_session, ingestion_id=ing.id, company_id=company.id
        )
    db_session.refresh(ing)
    # left mid-pipeline for a retry to pick up -- not marked failed.
    assert ing.status == IngestionStatus.extracting


def test_unsupported_content_type_marks_failed(
    db_session: Session,
) -> None:
    company = make_company(db_session)
    key = f"{company.id}/{uuid4()}/original.bin"
    storage.save_bytes(key, b"irrelevant")
    ing = Ingestion(
        company_id=company.id,
        source=IngestionSource.photo,
        s3_object_key=key,
        content_type="text/plain",
    )
    db_session.add(ing)
    db_session.commit()

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.failed


def test_matched_with_default_ledger_auto_posts_voucher(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 wedge: with a default purchase ledger configured and a
    known uploader, a clean matched extraction builds and posts the
    2-line Purchase voucher instead of stopping at `matched`."""
    user = make_user(db_session)
    company = make_company(db_session)
    party_ledger = Ledger(
        company_id=company.id,
        name="Sharma Traders",
        name_normalized="sharma traders",
        group_name="Sundry Creditors",
        gstin="27AAACR5055K1Z8",
    )
    purchase_ledger = Ledger(
        company_id=company.id,
        name="Purchases",
        name_normalized="purchases",
        group_name="Purchase Accounts",
    )
    db_session.add_all([party_ledger, purchase_ledger])
    db_session.commit()
    company.default_purchase_ledger_id = purchase_ledger.id
    db_session.commit()
    ing = _seed_ingestion(db_session, company, created_by=user)

    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "Sharma Traders",
            "vendor_gstin": "27AAACR5055K1Z8",
            "invoice_number": "INV-001",
            "total_amount": "11800.00",
            "subtotal": "10000.00",
            "cgst": "900.00",
            "sgst": "900.00",
            "extraction_confidence": 0.97,
            "is_likely_invoice": True,
        }
    )
    _patch_extract(monkeypatch, invoice)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.posted
    assert ing.posted_voucher_id is not None

    voucher = (
        db_session.query(Voucher).filter(Voucher.id == ing.posted_voucher_id).one()
    )
    assert voucher.voucher_type.value == "Purchase"
    assert voucher.is_optional_in_tally is True
    assert voucher.is_auto_posted is True
    assert voucher.source == "photo"
    assert voucher.source_ingestion_id == ing.id
    assert voucher.total_amount == 11800
    entries = {e.ledger_id: e.entry_type.value for e in voucher.entries}
    assert entries[purchase_ledger.id] == "Dr"
    assert entries[party_ledger.id] == "Cr"


def test_matched_without_uploader_stays_matched_no_voucher(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default ledger alone isn't enough -- an ingestion with no known
    `created_by` (e.g. a future non-mobile channel) must not crash the
    pipeline; it just stops at `matched` like before this feature."""
    company = make_company(db_session)
    party_ledger = Ledger(
        company_id=company.id,
        name="Sharma Traders",
        name_normalized="sharma traders",
        group_name="Sundry Creditors",
        gstin="27AAACR5055K1Z8",
    )
    purchase_ledger = Ledger(
        company_id=company.id,
        name="Purchases",
        name_normalized="purchases",
        group_name="Purchase Accounts",
    )
    db_session.add_all([party_ledger, purchase_ledger])
    db_session.commit()
    company.default_purchase_ledger_id = purchase_ledger.id
    db_session.commit()
    ing = _seed_ingestion(db_session, company)  # no created_by

    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "Sharma Traders",
            "vendor_gstin": "27AAACR5055K1Z8",
            "total_amount": "11800.00",
            "subtotal": "10000.00",
            "cgst": "900.00",
            "sgst": "900.00",
            "extraction_confidence": 0.97,
            "is_likely_invoice": True,
        }
    )
    _patch_extract(monkeypatch, invoice)

    extraction_pipeline.run_extraction(
        db_session, ingestion_id=ing.id, company_id=company.id
    )
    db_session.refresh(ing)

    assert ing.status == IngestionStatus.matched
    assert ing.posted_voucher_id is None
