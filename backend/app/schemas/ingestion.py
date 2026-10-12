"""Ingestion schemas (Phase 1 invoice-scan wedge)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import StringConstraints

from app.schemas.common import TaxMindBooksBase

IngestionSourceStr = Annotated[
    str, StringConstraints(pattern=r"^(photo|pdf)$")
]


class IngestionOut(TaxMindBooksBase):
    id: UUID
    company_id: UUID
    source: str
    status: str
    content_type: str | None
    content_size: int | None
    received_at: datetime
    extracted_at: datetime | None
    failed_at: datetime | None
    failure_reason: str | None
    posted_voucher_id: UUID | None
    parsed_data: dict[str, Any] | None
    extraction_confidence: Decimal | None
    extraction_flags: list[str]
    created_at: datetime


class IngestionListItem(TaxMindBooksBase):
    id: UUID
    source: str
    status: str
    content_type: str | None
    extraction_confidence: Decimal | None
    extraction_flags: list[str]
    received_at: datetime


class IngestionListResponse(TaxMindBooksBase):
    items: list[IngestionListItem]
    meta: dict[str, Any]
