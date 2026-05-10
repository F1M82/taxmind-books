"""Ledger-flow schemas (P0.17)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import EmailStr, StringConstraints
from typing_extensions import Annotated

from app.schemas.common import Money, TaxMindBooksBase

GstinStr = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    ),
]
PanStr = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"),
]
PhoneStr = Annotated[
    str, StringConstraints(pattern=r"^[+]?[0-9]{10,15}$")
]
StateCodeStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")]
BalanceTypeStr = Annotated[str, StringConstraints(pattern=r"^(Dr|Cr)$")]


class LedgerCreate(TaxMindBooksBase):
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    group_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    opening_balance: Money = Decimal("0.00")
    balance_type: BalanceTypeStr = "Dr"
    gstin: GstinStr | None = None
    pan: PanStr | None = None
    phone: PhoneStr | None = None
    email: EmailStr | None = None
    address: str | None = None
    state_code: StateCodeStr | None = None
    parent_ledger_id: UUID | None = None


class LedgerUpdate(TaxMindBooksBase):
    name: (
        Annotated[str, StringConstraints(min_length=1, max_length=255)] | None
    ) = None
    group_name: Annotated[str, StringConstraints(max_length=100)] | None = None
    opening_balance: Money | None = None
    balance_type: BalanceTypeStr | None = None
    gstin: GstinStr | None = None
    pan: PanStr | None = None
    phone: PhoneStr | None = None
    email: EmailStr | None = None
    address: str | None = None
    state_code: StateCodeStr | None = None
    is_active: bool | None = None


class LedgerOut(TaxMindBooksBase):
    id: UUID
    company_id: UUID
    name: str
    name_normalized: str
    group_name: str | None = None
    parent_ledger_id: UUID | None = None
    opening_balance: Money
    balance_type: str
    gstin: str | None = None
    pan: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    state_code: str | None = None
    is_active: bool
    tally_master_id: str | None = None
    tally_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LedgerListItem(TaxMindBooksBase):
    id: UUID
    name: str
    group_name: str | None = None
    opening_balance: Money
    balance_type: str
    gstin: str | None = None
    is_active: bool


class LedgerListResponse(TaxMindBooksBase):
    items: list[LedgerListItem]
    meta: dict[str, str | int | None]
