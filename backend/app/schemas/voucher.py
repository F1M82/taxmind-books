"""Voucher schemas (P0.18: create)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, StringConstraints
from typing_extensions import Annotated

from app.schemas.common import Money, TaxMindBooksBase

VoucherTypeStr = Annotated[
    str,
    StringConstraints(
        pattern=r"^(Receipt|Payment|Sales|Purchase|Journal|Contra|Debit Note|Credit Note)$"
    ),
]
EntryTypeStr = Annotated[str, StringConstraints(pattern=r"^(Dr|Cr)$")]
StateCodeStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")]


class VoucherEntryCreate(TaxMindBooksBase):
    ledger_id: UUID
    amount: Money
    entry_type: EntryTypeStr
    narration: str | None = None
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100)
    cgst: Money | None = None
    sgst: Money | None = None
    igst: Money | None = None
    tds_amount: Money | None = None
    tds_section: Annotated[str, StringConstraints(max_length=10)] | None = None


class VoucherCreate(TaxMindBooksBase):
    voucher_type: VoucherTypeStr
    voucher_number: Annotated[str, StringConstraints(max_length=50)] | None = (
        None
    )
    date: date
    narration: str | None = None
    reference: Annotated[str, StringConstraints(max_length=100)] | None = None
    total_amount: Money
    entries: list[VoucherEntryCreate] = Field(min_length=2)
    # GST
    gst_applicable: bool = False
    place_of_supply: StateCodeStr | None = None
    cgst: Money = Decimal("0.00")
    sgst: Money = Decimal("0.00")
    igst: Money = Decimal("0.00")
    cess: Money = Decimal("0.00")
    # TDS
    tds_applicable: bool = False
    tds_amount: Money = Decimal("0.00")
    tds_section: Annotated[str, StringConstraints(max_length=10)] | None = None


class VoucherEntryOut(TaxMindBooksBase):
    id: UUID
    ledger_id: UUID
    amount: Money
    entry_type: str
    line_number: int
    narration: str | None = None
    gst_rate: Decimal | None = None
    cgst: Money | None = None
    sgst: Money | None = None
    igst: Money | None = None
    tds_amount: Money | None = None
    tds_section: str | None = None


class VoucherOut(TaxMindBooksBase):
    id: UUID
    company_id: UUID
    voucher_type: str
    voucher_number: str | None = None
    date: date
    narration: str | None = None
    reference: str | None = None
    total_amount: Money
    status: str
    source: str
    is_auto_posted: bool
    confidence_score: Decimal | None = None
    gst_applicable: bool
    place_of_supply: str | None = None
    cgst: Money
    sgst: Money
    igst: Money
    cess: Money
    tds_applicable: bool
    tds_amount: Money
    tds_section: str | None = None
    tally_posted_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime
    entries: list[VoucherEntryOut]
