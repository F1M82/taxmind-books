"""Auth-flow schemas.

Mirrors `docs/API.md` §"Authentication". Phase 0 starts with register
only; login / refresh / me / password change land in P0.15.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints
from typing_extensions import Annotated

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
