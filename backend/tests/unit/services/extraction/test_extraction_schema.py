"""Unit tests for the extraction Pydantic contract."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.services.extraction.extraction_schema import (
    ExtractedInvoice,
    InvoiceLineItem,
)
from pydantic import ValidationError


def test_parses_a_typical_invoice() -> None:
    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "Sharma Traders",
            "vendor_gstin": "27AAACR5055K1Z8",
            "invoice_number": "INV-001",
            "invoice_date": "2026-05-07",
            "line_items": [
                {
                    "description": "Steel rods",
                    "amount": "10000.00",
                    "gst_rate": "18",
                }
            ],
            "subtotal": "10000.00",
            "cgst": "900.00",
            "sgst": "900.00",
            "total_amount": "11800.00",
            "extraction_confidence": 0.95,
            "is_likely_invoice": True,
        }
    )
    assert invoice.vendor_gstin == "27AAACR5055K1Z8"
    assert invoice.total_amount == Decimal("11800.00")
    assert invoice.line_items[0].amount == Decimal("10000.00")


def test_date_parsing_dd_mm_not_confused() -> None:
    invoice = ExtractedInvoice.model_validate(
        {
            "invoice_date": "2026-05-07",
            "total_amount": "100.00",
            "extraction_confidence": 0.9,
            "is_likely_invoice": True,
        }
    )
    assert invoice.invoice_date.month == 5
    assert invoice.invoice_date.day == 7


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(
            {
                "total_amount": "100.00",
                "extraction_confidence": 0.9,
                "is_likely_invoice": True,
                "unexpected_field": "x",
            }
        )


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(
            {
                "total_amount": "100.00",
                "extraction_confidence": 1.5,
                "is_likely_invoice": True,
            }
        )


def test_not_an_invoice_shape() -> None:
    invoice = ExtractedInvoice.model_validate(
        {
            "total_amount": "0.00",
            "extraction_confidence": 0.1,
            "is_likely_invoice": False,
            "extraction_warnings": ["image shows a selfie, not an invoice"],
        }
    )
    assert invoice.is_likely_invoice is False
    assert invoice.total_amount == Decimal("0.00")


def test_line_item_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InvoiceLineItem.model_validate(
            {"description": "x", "amount": "1.00", "bogus": 1}
        )


def test_money_rejects_thousand_separators() -> None:
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(
            {
                "total_amount": "1,500.00",
                "extraction_confidence": 0.9,
                "is_likely_invoice": True,
            }
        )
