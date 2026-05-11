"""Connector enrollment + status schemas (P0.23)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.common import TaxMindBooksBase


class EnrollmentCodeOut(TaxMindBooksBase):
    """Response from POST /api/v1/connector/enrollment-codes.

    The raw `code` is returned exactly once; only its hash is stored.
    """

    code: str
    expires_at: datetime
    company_id: UUID


class EnrollRequest(TaxMindBooksBase):
    code: str


class EnrollResponse(TaxMindBooksBase):
    connector_id: UUID
    company_id: UUID
    connector_token: str
    expires_in_days: int
