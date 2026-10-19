"""Unit tests for Rules V1-V9 (docs/EXTRACTION_CONTRACT.md)."""

from __future__ import annotations

from datetime import date

import pytest
from app.services.extraction.extraction_schema import ExtractedInvoice
from app.services.extraction.validator import NotAnInvoice, validate


def _invoice(**overrides) -> ExtractedInvoice:  # type: ignore[no-untyped-def]
    base = {
        "vendor_name": "Sharma Traders",
        "vendor_gstin": "27AAACR5055K1Z8",
        "invoice_date": "2026-05-07",
        "subtotal": "10000.00",
        "cgst": "900.00",
        "sgst": "900.00",
        "total_amount": "11800.00",
        "extraction_confidence": 0.95,
        "is_likely_invoice": True,
    }
    base.update(overrides)
    return ExtractedInvoice.model_validate(base)


def test_clean_invoice_is_high_confidence_no_flags() -> None:
    result = validate(_invoice(), today=date(2026, 5, 8))
    assert result.flags == []
    assert result.needs_review is False
    assert result.confidence == pytest.approx(0.95)


def test_v1_invalid_gstin_format_nulled_and_flagged() -> None:
    result = validate(
        _invoice(vendor_gstin="NOT-A-GSTIN", cgst=None, sgst=None),
        today=date(2026, 5, 8),
    )
    assert result.vendor_gstin is None
    assert "vendor_gstin_invalid_format" in result.flags
    assert result.needs_review is True  # critical flag


def test_v2_pan_derived_from_gstin() -> None:
    result = validate(_invoice(), today=date(2026, 5, 8))
    assert result.vendor_pan == "AAACR5055K"  # gstin[2:12]


def test_v3_state_code_derived_from_gstin() -> None:
    result = validate(_invoice(), today=date(2026, 5, 8))
    assert result.vendor_state_code == "27"


def test_v3_state_code_conflict_flagged_but_gstin_wins() -> None:
    result = validate(
        _invoice(vendor_state_code="09"), today=date(2026, 5, 8)
    )
    assert "vendor_state_code_conflict" in result.flags
    assert result.vendor_state_code == "27"


def test_v4_gst_math_mismatch_flagged() -> None:
    result = validate(
        _invoice(total_amount="99999.00"), today=date(2026, 5, 8)
    )
    assert "gst_math_mismatch" in result.flags
    assert result.needs_review is True


def test_v4_gst_math_within_tolerance_not_flagged() -> None:
    # expected = 10000 + 900 + 900 = 11800; off by 0.50, within ₹1 tolerance
    result = validate(
        _invoice(total_amount="11800.50"), today=date(2026, 5, 8)
    )
    assert "gst_math_mismatch" not in result.flags


def test_v5_cgst_sgst_split_mismatch() -> None:
    result = validate(
        _invoice(cgst="900.00", sgst="500.00", total_amount="11400.00"),
        today=date(2026, 5, 8),
    )
    assert "gst_split_mismatch" in result.flags


def test_v6_future_date_implausible() -> None:
    result = validate(
        _invoice(invoice_date="2026-05-20"), today=date(2026, 5, 8)
    )
    assert "date_implausible" in result.flags


def test_v6_too_far_in_past_implausible() -> None:
    result = validate(
        _invoice(invoice_date="2020-01-01"), today=date(2026, 5, 8)
    )
    assert "date_implausible" in result.flags


def test_v7_self_invoice_detected_as_sales() -> None:
    result = validate(
        _invoice(buyer_gstin="27AAACR5055K1Z8"),
        today=date(2026, 5, 8),
        active_company_gstin="27AAACR5055K1Z8",
    )
    assert "self_invoice" in result.flags
    assert result.suggested_voucher_type == "Sales"


def test_no_gstin_flagged() -> None:
    result = validate(
        _invoice(vendor_gstin=None, cgst=None, sgst=None),
        today=date(2026, 5, 8),
    )
    assert "no_gstin" in result.flags


def test_v8_not_an_invoice_raises() -> None:
    with pytest.raises(NotAnInvoice):
        validate(
            _invoice(
                total_amount="0.00",
                is_likely_invoice=False,
                cgst=None,
                sgst=None,
            ),
            today=date(2026, 5, 8),
        )


def test_v9_confidence_floored_by_critical_flags() -> None:
    result = validate(
        _invoice(total_amount="99999.00"),  # 1 critical: gst_math_mismatch
        today=date(2026, 5, 8),
    )
    assert result.confidence == pytest.approx(0.95 * (1 - 0.10 * 1))


def test_v9_confidence_floored_by_noncritical_flags() -> None:
    result = validate(
        _invoice(vendor_gstin=None, cgst=None, sgst=None),  # no_gstin: non-critical
        today=date(2026, 5, 8),
    )
    assert result.confidence == pytest.approx(0.95 - 0.05 * 1)


def test_v9_confidence_clamped_at_zero() -> None:
    # 3 critical flags (bad vendor_gstin format, gst math mismatch,
    # implausible date) + 2 non-critical (no_gstin once vendor_gstin is
    # nulled, bad buyer_gstin format) drives the raw formula negative.
    result = validate(
        _invoice(
            extraction_confidence=0.05,
            vendor_gstin="BAD",
            buyer_gstin="ALSO-BAD",
            total_amount="99999.00",
            invoice_date="2020-01-01",
        ),
        today=date(2026, 5, 8),
    )
    assert result.confidence == 0.0
