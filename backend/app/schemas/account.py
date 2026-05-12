"""Account-lifecycle schemas (P0.45).

Shapes mirror docs/API.md §"Account & Data Lifecycle".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

from app.schemas.common import TaxMindBooksBase


class AccountDeletionCreateRequest(TaxMindBooksBase):
    # Free-text reason is optional and never reaches the audit log
    # (it's not financially significant and may itself be PII).
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


class AccountDeletionResponse(TaxMindBooksBase):
    id: UUID
    status: str
    requested_at: datetime
    grace_ends_at: datetime


class AccountDeletionCancelResponse(TaxMindBooksBase):
    status: str
    cancelled_at: datetime
