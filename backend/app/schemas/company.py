"""Company-flow schemas (P0.16)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints
from typing_extensions import Annotated

from app.schemas.common import TaxMindBooksBase

# GSTIN: 15 chars, format from SCHEMA.sql ck_companies_gstin_format.
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
PincodeStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
StateCodeStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}$")]
RoleStr = Annotated[
    str, StringConstraints(pattern=r"^(owner|admin|accountant|viewer)$")
]
AccountingSourceStr = Annotated[
    str,
    StringConstraints(
        pattern=r"^(standalone|tally|zoho|quickbooks|busy)$"
    ),
]


class CompanyCreate(TaxMindBooksBase):
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    gstin: GstinStr | None = None
    pan: PanStr | None = None
    financial_year_start: date | None = None
    address: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state_code: StateCodeStr | None = None
    pincode: PincodeStr | None = None
    accounting_source: AccountingSourceStr = "standalone"


class CompanyUpdate(TaxMindBooksBase):
    name: (
        Annotated[str, StringConstraints(min_length=1, max_length=255)] | None
    ) = None
    gstin: GstinStr | None = None
    pan: PanStr | None = None
    address: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    state_code: StateCodeStr | None = None
    pincode: PincodeStr | None = None
    accounting_source: AccountingSourceStr | None = None


class CompanyOut(TaxMindBooksBase):
    id: UUID
    name: str
    gstin: str | None = None
    pan: str | None = None
    financial_year_start: date
    status: str
    address: str | None = None
    city: str | None = None
    state_code: str | None = None
    pincode: str | None = None
    accounting_source: str
    created_at: datetime
    your_role: str = Field(description="Caller's role on this company")


class CompanyListItem(TaxMindBooksBase):
    id: UUID
    name: str
    gstin: str | None = None
    status: str
    your_role: str


class PaginationMeta(TaxMindBooksBase):
    next_cursor: str | None = None
    total: int


class CompanyListResponse(TaxMindBooksBase):
    items: list[CompanyListItem]
    meta: PaginationMeta


# ---------- Members ----------


class MemberAddRequest(TaxMindBooksBase):
    email: EmailStr
    role: RoleStr


class MemberOut(TaxMindBooksBase):
    id: UUID
    user_id: UUID
    company_id: UUID
    role: str
    user_email: EmailStr
    created_at: datetime
