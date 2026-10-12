"""Business-rule validation on top of the LLM's raw extraction.

Implements Rules V1-V9 from docs/EXTRACTION_CONTRACT.md. Pure functions,
no I/O -- the LLM call and party matching happen elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.services.extraction.extraction_schema import ExtractedInvoice

GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

CRITICAL_FLAGS = frozenset(
    {"gst_math_mismatch", "date_implausible", "vendor_gstin_invalid_format"}
)

GST_MATH_TOLERANCE = Decimal("1.00")
MAX_INVOICE_AGE_DAYS = 2 * 365


class NotAnInvoice(ValueError):
    """Rule V8: total_amount absent/zero and is_likely_invoice=false."""


@dataclass
class ValidationResult:
    vendor_gstin: str | None
    vendor_pan: str | None
    vendor_state_code: str | None
    flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    needs_review: bool = False
    suggested_voucher_type: str = "Purchase"


def _gstin_valid(gstin: str) -> bool:
    return bool(GSTIN_RE.match(gstin))


def validate(
    extracted: ExtractedInvoice,
    *,
    today: date | None = None,
    active_company_gstin: str | None = None,
) -> ValidationResult:
    today = today or date.today()
    flags: list[str] = list(extracted.extraction_warnings)

    if (
        extracted.total_amount is None or extracted.total_amount <= 0
    ) and not extracted.is_likely_invoice:
        raise NotAnInvoice("total_amount missing/zero and not likely an invoice")

    # V1: GSTIN format
    vendor_gstin = extracted.vendor_gstin
    if vendor_gstin is not None and not _gstin_valid(vendor_gstin):
        flags.append("vendor_gstin_invalid_format")
        vendor_gstin = None
    buyer_gstin = extracted.buyer_gstin
    if buyer_gstin is not None and not _gstin_valid(buyer_gstin):
        flags.append("buyer_gstin_invalid_format")
        buyer_gstin = None

    # V2: PAN derivation
    vendor_pan = extracted.vendor_pan
    if vendor_gstin is not None and vendor_pan is None:
        vendor_pan = vendor_gstin[2:12]

    # V3: state code derivation (prefer GSTIN-derived on conflict)
    vendor_state_code = extracted.vendor_state_code
    if vendor_gstin is not None:
        gstin_state = vendor_gstin[0:2]
        if vendor_state_code is not None and vendor_state_code != gstin_state:
            flags.append("vendor_state_code_conflict")
        vendor_state_code = gstin_state

    # V4: GST math
    gst_total = sum(
        v or Decimal("0")
        for v in (extracted.cgst, extracted.sgst, extracted.igst)
    )
    gst_applicable = gst_total > 0
    if gst_applicable and extracted.total_amount is not None:
        expected_total = (
            (extracted.subtotal or Decimal("0"))
            - (extracted.discount or Decimal("0"))
            + (extracted.cgst or Decimal("0"))
            + (extracted.sgst or Decimal("0"))
            + (extracted.igst or Decimal("0"))
            + (extracted.cess or Decimal("0"))
            + (extracted.round_off or Decimal("0"))
        )
        if abs(expected_total - extracted.total_amount) > GST_MATH_TOLERANCE:
            flags.append("gst_math_mismatch")

    # V5: CGST == SGST for intra-state supplies
    if (
        extracted.cgst is not None
        and extracted.sgst is not None
        and abs(extracted.cgst - extracted.sgst) > GST_MATH_TOLERANCE
    ):
        flags.append("gst_split_mismatch")

    # V6: date plausibility
    if extracted.invoice_date is not None and (
        extracted.invoice_date > today + timedelta(days=1)
        or (today - extracted.invoice_date).days > MAX_INVOICE_AGE_DAYS
    ):
        flags.append("date_implausible")

    # V7: self-billing detection
    suggested_voucher_type = "Purchase"
    if (
        active_company_gstin
        and vendor_gstin == buyer_gstin == active_company_gstin
    ):
        flags.append("self_invoice")
        suggested_voucher_type = "Sales"

    if not vendor_gstin:
        flags.append("no_gstin")

    # V9: confidence flooring
    critical = [f for f in flags if f in CRITICAL_FLAGS]
    non_critical = [f for f in flags if f not in CRITICAL_FLAGS]
    confidence = extracted.extraction_confidence * (
        1 - 0.10 * len(critical)
    ) - 0.05 * len(non_critical)
    confidence = max(0.0, min(1.0, confidence))

    needs_review = bool(critical) or confidence < 0.90

    return ValidationResult(
        vendor_gstin=vendor_gstin,
        vendor_pan=vendor_pan,
        vendor_state_code=vendor_state_code,
        flags=flags,
        confidence=confidence,
        needs_review=needs_review,
        suggested_voucher_type=suggested_voucher_type,
    )
