"""Onboarding checklist composition (P0.42).

Each item is derived from existing tables — there is deliberately no
`onboarding_state` table per docs/API.md §"Onboarding". The four
items map to one small query each:

  company_created          ← `companies.created_at` (always set for
                             the active company; the dependency
                             already provides it)
  connector_installed      ← either of two independent ways a
                             company becomes Tally-connected:
                             (a) any `connector_enrollment_codes`
                             row for the company with `consumed_at
                             IS NOT NULL` (the enrollment ceremony
                             was run for it), or (b) `Company.
                             tally_master_id IS NOT NULL` (it was
                             connected via the discovery/mapping
                             flow instead — see CLAUDE.md "Tally
                             company mapping: two representations
                             that can drift"). We use MIN(consumed_at)
                             for (a) so re-pairings don't reset the
                             timestamp.
  ledgers_synced           ← any `ledgers` row with `tally_synced_at
                             IS NOT NULL`. Manually-created ledgers
                             don't count; the checklist item is
                             "Sync ledgers from Tally" specifically.
  first_voucher_posted     ← any voucher with `status='posted'`.
                             We use the DB-side "posted" status
                             rather than `tally_posted_at` so a
                             user can tick this without the
                             connector being online — matches the
                             item label "Post your first voucher"
                             (the user's action, not the round-trip
                             outcome).

`build_checklist` returns plain dataclasses; the API layer wraps
them in the Pydantic response schema. The service is read-only and
audit-exempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.connector import ConnectorCompanyBinding
from app.models.connector_enrollment import ConnectorEnrollmentCode
from app.models.ledger import Ledger
from app.models.voucher import Voucher, VoucherStatus


@dataclass
class ChecklistItem:
    key: str
    label: str
    completed: bool
    completed_at: datetime | None = None


@dataclass
class ChecklistData:
    company_id: str
    items: list[ChecklistItem] = field(default_factory=list)

    @property
    def completed_count(self) -> int:
        return sum(1 for i in self.items if i.completed)

    @property
    def total_count(self) -> int:
        return len(self.items)


def build_checklist(  # audit-exempt: read-only aggregation
    db: Session, *, company: Company
) -> ChecklistData:
    """Compose the five-item checklist for `company`."""
    items: list[ChecklistItem] = []

    items.append(
        ChecklistItem(
            key="company_created",
            label="Create your company",
            completed=True,
            completed_at=company.created_at,
        )
    )

    enrolled_at = db.scalar(
        select(func.min(ConnectorEnrollmentCode.consumed_at)).where(
            ConnectorEnrollmentCode.company_id == company.id,
            ConnectorEnrollmentCode.consumed_at.isnot(None),
        )
    )
    mapped_at = None
    if company.tally_master_id is not None:
        mapped_at = db.scalar(
            select(func.min(ConnectorCompanyBinding.configured_at)).where(
                ConnectorCompanyBinding.company_id == company.id,
            )
        )
    items.append(
        ChecklistItem(
            key="connector_installed",
            label="Install Tally Connector",
            completed=enrolled_at is not None or company.tally_master_id is not None,
            completed_at=enrolled_at or mapped_at,
        )
    )

    first_sync = db.scalar(
        select(func.min(Ledger.tally_synced_at)).where(
            Ledger.company_id == company.id,
            Ledger.tally_synced_at.isnot(None),
        )
    )
    items.append(
        ChecklistItem(
            key="ledgers_synced",
            label="Sync ledgers from Tally",
            completed=first_sync is not None,
            completed_at=first_sync,
        )
    )

    first_voucher = db.scalar(
        select(func.min(Voucher.created_at)).where(
            Voucher.company_id == company.id,
            # P0.46d: "first voucher posted" is a user-action milestone;
            # the user has done their part the moment the row exists,
            # regardless of whether Tally has mirrored it.
            Voucher.status.in_(
                [VoucherStatus.posted, VoucherStatus.pending_tally_post]
            ),
        )
    )
    items.append(
        ChecklistItem(
            key="first_voucher_posted",
            label="Post your first voucher",
            completed=first_voucher is not None,
            completed_at=first_voucher,
        )
    )

    return ChecklistData(company_id=str(company.id), items=items)


__all__ = ["ChecklistData", "ChecklistItem", "build_checklist"]
