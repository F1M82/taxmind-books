"""Onboarding checklist schemas (P0.42).

Shape mirrors docs/API.md §"Onboarding". Items are returned in a
fixed order; the client is free to render them in any order but the
server's ordering matches the user's chronological progress.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.schemas.common import TaxMindBooksBase

OnboardingItemKey = Literal[
    "company_created",
    "connector_installed",
    "ledgers_synced",
    "first_voucher_posted",
    "first_invoice_extracted",
]


class OnboardingItem(TaxMindBooksBase):
    key: OnboardingItemKey
    label: str
    completed: bool
    completed_at: datetime | None = None


class OnboardingChecklistResponse(TaxMindBooksBase):
    company_id: UUID
    items: list[OnboardingItem]
    completed_count: int
    total_count: int
