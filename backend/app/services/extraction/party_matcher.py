"""Party matching: suggest a ledger for an extracted invoice's vendor.

Per docs/EXTRACTION_CONTRACT.md "Party matching". Reuses the fuzzy-name
infrastructure `LedgerService.list` already built (pg_trgm similarity
on `Ledger.name_normalized`) rather than re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ledger import Ledger
from app.services.ledger_service import normalize_ledger_name

FUZZY_SIMILARITY_THRESHOLD = 0.6
FUZZY_CANDIDATE_LIMIT = 3


@dataclass
class PartyMatch:
    ledger_id: UUID | None
    flags: list[str]


def match_party(
    db: Session,
    *,
    company_id: UUID,
    vendor_gstin: str | None,
    vendor_pan: str | None,
    vendor_name: str | None,
) -> PartyMatch:
    base = db.query(Ledger).filter(
        Ledger.company_id == company_id, Ledger.is_active.is_(True)
    )

    # Tier 1: GSTIN exact match
    if vendor_gstin:
        ledger = base.filter(Ledger.gstin == vendor_gstin).first()
        if ledger is not None:
            return PartyMatch(ledger_id=ledger.id, flags=[])

    # Tier 2: PAN exact match
    if vendor_pan:
        ledger = base.filter(Ledger.pan == vendor_pan).first()
        if ledger is not None:
            return PartyMatch(
                ledger_id=ledger.id, flags=["matched_via_pan_not_gstin"]
            )

    # Tier 3: fuzzy name match (top candidate; ≥0.6 similarity)
    if vendor_name:
        term = normalize_ledger_name(vendor_name)
        candidate = (
            base.filter(
                func.similarity(Ledger.name_normalized, term)
                >= FUZZY_SIMILARITY_THRESHOLD
            )
            .order_by(func.similarity(Ledger.name_normalized, term).desc())
            .first()
        )
        if candidate is not None:
            return PartyMatch(
                ledger_id=candidate.id, flags=["fuzzy_party_match"]
            )

    # Tier 4: no match
    return PartyMatch(ledger_id=None, flags=["new_party"])
