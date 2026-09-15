"""P3.2 — opening-balance seed operation.

Implements ``docs/PHASE_3_OPENING_BALANCE_ARCHITECTURE.md``: establish each
ledger's ``opening_balance``/``balance_type`` ONCE, at the anchor date (the
start of the earliest imported financial year), from a Tally Trial Balance.
Never re-written on a later run; never overwrites a value the seed didn't
itself write (a Phase A direct-entry opening, or a manual correction made
through the normal ledger-update endpoint).

Read-only against Tally -- the caller already pulled the Trial Balance via
the connector's ``get_trial_balance`` command; this module only classifies
and persists the reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import OpeningBalanceAnchorMismatch
from app.models.ledger import BalanceType, Ledger
from app.services.tally.company_mapping import require_safe_company_mapping

_MAX_LISTED = 20


def _normalize(name: str) -> str:
    return name.strip().lower()


def _signed_to_pair(signed: Decimal) -> tuple[Decimal, BalanceType]:
    """Map a Tally-signed closing balance to (magnitude, Dr/Cr).

    Tally's ``CLOSINGBALANCE`` and TaxMind's own report engine
    (``reporting.trial_balance._signed_to_pair``) share one convention:
    positive = Dr, negative = Cr, zero = Dr.
    """
    if signed >= 0:
        return signed, BalanceType.Dr
    return -signed, BalanceType.Cr


@dataclass
class OpeningBalanceSeedResult:
    """Outcome of one seed run. Counts only -- no finance data leaks into
    the response beyond ledger names already visible to the caller."""

    anchor_date: date
    seeded: int = 0
    skipped_already_seeded: int = 0
    skipped_not_tally_synced: int = 0
    skipped_conflict: list[str] = field(default_factory=list)
    unmatched_tally_rows: list[str] = field(default_factory=list)
    malformed_rows: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_date": self.anchor_date.isoformat(),
            "seeded": self.seeded,
            "skipped_already_seeded": self.skipped_already_seeded,
            "skipped_not_tally_synced": self.skipped_not_tally_synced,
            "skipped_conflict_count": len(self.skipped_conflict),
            "skipped_conflict": self.skipped_conflict[:_MAX_LISTED],
            "unmatched_tally_rows_count": len(self.unmatched_tally_rows),
            "unmatched_tally_rows": self.unmatched_tally_rows[:_MAX_LISTED],
            "malformed_rows": self.malformed_rows[:_MAX_LISTED],
        }


def seed_opening_balances(
    db: Session,
    audit: AuditEmitter,
    *,
    company_id: UUID,
    anchor_date: date,
    tally_company_guid: str | None,
    rows: list[dict[str, Any]],
) -> OpeningBalanceSeedResult:
    """Idempotently seed each ledger's anchor opening balance.

    Fail-closed on company identity -- the same gate ``sync_masters``
    uses: the Trial Balance reply must carry the Tally company GUID and
    it must match ``Company.tally_master_id``, or nothing is written
    (raises ``CompanyMappingError``).

    The anchor is locked on first successful run: once
    ``Company.opening_balance_anchor_date`` is set, a later call with a
    *different* ``anchor_date`` raises ``OpeningBalanceAnchorMismatch``
    rather than silently re-anchoring -- re-anchoring would corrupt every
    other FY's reports (see the architecture doc's "why a single field
    is correct").

    Per ledger, exactly one of four things happens:
      - not matched by name to any Tally row -> unmatched, untouched
      - matched but not Tally-synced (no ``tally_master_id``) -> skipped
      - matched, Tally-synced, ``opening_balance_seeded_at`` already set
        -> already seeded, no-op (this is what makes a same-anchor
        re-run safe to run twice)
      - matched, Tally-synced, never seeded, ``opening_balance`` still 0
        -> written from the Trial Balance row, ``opening_balance_seeded_at``
        stamped, audited
    A matched, Tally-synced, never-seeded ledger whose ``opening_balance``
    is already non-zero is a conflict (someone set a value outside the
    seed) and is left untouched, reported for operator review.
    """
    company = require_safe_company_mapping(
        db, company_id=company_id, tally_company_guid=tally_company_guid
    )

    if (
        company.opening_balance_anchor_date is not None
        and company.opening_balance_anchor_date != anchor_date
    ):
        raise OpeningBalanceAnchorMismatch(
            "company already has a different opening-balance anchor date; "
            "refusing to re-anchor",
            details={
                "existing_anchor_date": (
                    company.opening_balance_anchor_date.isoformat()
                ),
                "requested_anchor_date": anchor_date.isoformat(),
            },
        )

    result = OpeningBalanceSeedResult(anchor_date=anchor_date)

    ledgers_by_name: dict[str, Ledger] = {
        ledger.name_normalized: ledger
        for ledger in db.query(Ledger)
        .filter(Ledger.company_id == company_id)
        .all()
    }

    for row in rows:
        raw_name = row.get("name")
        if not raw_name:
            result.malformed_rows.append(str(row))
            continue
        ledger = ledgers_by_name.get(_normalize(str(raw_name)))
        if ledger is None:
            result.unmatched_tally_rows.append(str(raw_name))
            continue

        if ledger.tally_master_id is None:
            result.skipped_not_tally_synced += 1
            continue
        if ledger.opening_balance_seeded_at is not None:
            result.skipped_already_seeded += 1
            continue
        if ledger.opening_balance != 0:
            result.skipped_conflict.append(ledger.name)
            continue

        try:
            signed = Decimal(str(row.get("closing_balance", "0")))
        except (InvalidOperation, TypeError):
            result.malformed_rows.append(str(raw_name))
            continue

        magnitude, balance_type = _signed_to_pair(signed)
        old_value = {
            "opening_balance": str(ledger.opening_balance),
            "balance_type": ledger.balance_type.value,
        }
        ledger.opening_balance = magnitude
        ledger.balance_type = balance_type
        ledger.opening_balance_seeded_at = datetime.now(UTC)
        db.flush()
        audit.emit(
            action="ledger.opening_balance_seeded",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=old_value,
            new_value={
                "opening_balance": str(ledger.opening_balance),
                "balance_type": ledger.balance_type.value,
                "anchor_date": anchor_date.isoformat(),
            },
        )
        result.seeded += 1

    if company.opening_balance_anchor_date is None:
        company.opening_balance_anchor_date = anchor_date
    company.opening_balance_seeded_at = datetime.now(UTC)
    db.flush()

    return result
