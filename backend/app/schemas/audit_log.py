"""Audit-log read schemas (P0.20)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.common import TaxMindBooksBase


class AuditLogOut(TaxMindBooksBase):
    id: UUID
    user_id: UUID | None = None
    user_email: str | None = None
    action: str
    entity_type: str
    entity_id: UUID
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    changes: dict[str, Any]
    ip_address: str | None = None
    request_id: UUID | None = None
    source: str
    created_at: datetime


class AuditLogListResponse(TaxMindBooksBase):
    items: list[AuditLogOut]
    meta: dict[str, str | int | None]
