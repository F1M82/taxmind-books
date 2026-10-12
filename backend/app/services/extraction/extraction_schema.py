"""The LLM extraction contract (docs/EXTRACTION_CONTRACT.md).

Drift between the prompt (`prompts/invoice_v1.txt`) and this schema is
caught by `tests/unit/services/extraction/test_extraction_schema.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

SCHEMA_VERSION = "1.0"


def _parse_money(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        return Decimal(str(v))
    try:
        return Decimal(str(v))
    except InvalidOperation as exc:
        raise ValueError(f"not a valid amount: {v!r}") from exc


ExtractionMoney = Annotated[Decimal | None, BeforeValidator(_parse_money)]


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    hsn_sac: str | None = None
    quantity: str | None = None
    unit: str | None = None
    rate: ExtractionMoney = None
    amount: ExtractionMoney
    gst_rate: str | None = None


class ExtractedInvoice(BaseModel):
    """The contract: what the LLM must return."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None = None
    vendor_gstin: str | None = None
    vendor_pan: str | None = None
    vendor_address: str | None = None
    vendor_state_code: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    place_of_supply: str | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    subtotal: ExtractionMoney = None
    discount: ExtractionMoney = None
    cgst: ExtractionMoney = None
    sgst: ExtractionMoney = None
    igst: ExtractionMoney = None
    cess: ExtractionMoney = None
    round_off: ExtractionMoney = None
    total_amount: ExtractionMoney
    amount_in_words: str | None = None
    payment_terms: str | None = None
    notes: str | None = None
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)
    is_likely_invoice: bool
