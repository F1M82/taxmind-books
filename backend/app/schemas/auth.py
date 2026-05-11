"""Auth-flow schemas.

Mirrors `docs/API.md` §"Authentication". Phase 0 starts with register
only; login / refresh / me / password change land in P0.15.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints

from app.schemas.common import TaxMindBooksBase

# Phone format from API.md §register and SCHEMA.sql ck_users_phone_format.
PhoneStr = Annotated[
    str,
    StringConstraints(pattern=r"^[+]?[0-9]{10,15}$", strip_whitespace=True),
]

# Password length ≥ 12 per API.md §register Constraints.
PasswordStr = Annotated[
    str,
    StringConstraints(min_length=12, max_length=128),
]


class RegisterRequest(TaxMindBooksBase):
    email: EmailStr
    password: PasswordStr
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    phone: PhoneStr | None = None
    is_ca: bool = False
    firm_name: Annotated[str, StringConstraints(max_length=255)] | None = None
    ca_membership_no: Annotated[str, StringConstraints(max_length=50)] | None = (
        None
    )


class UserOut(TaxMindBooksBase):
    """Public view of a user — never includes hashed_password or phone."""

    id: UUID
    email: EmailStr
    full_name: str
    is_ca: bool
    firm_name: str | None = Field(default=None)
    is_active: bool
    created_at: datetime


class TokenUserOut(TaxMindBooksBase):
    """Trimmed user payload nested inside the /login response."""

    id: UUID
    email: EmailStr
    full_name: str
    is_ca: bool
    firm_name: str | None = None


class TokenResponse(TaxMindBooksBase):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: TokenUserOut


class RefreshRequest(TaxMindBooksBase):
    refresh_token: str


class CompanyMembershipOut(TaxMindBooksBase):
    id: UUID
    name: str
    role: str  # CompanyRole.value


class MeResponse(TaxMindBooksBase):
    """Response for GET /auth/me — user + their company memberships."""

    id: UUID
    email: EmailStr
    full_name: str
    is_ca: bool
    firm_name: str | None = None
    is_active: bool
    companies: list[CompanyMembershipOut]


class PasswordChangeRequest(TaxMindBooksBase):
    current_password: PasswordStr
    new_password: PasswordStr
