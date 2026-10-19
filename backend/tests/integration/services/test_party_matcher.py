"""Integration tests for party_matcher (needs a real DB for pg_trgm)."""

from __future__ import annotations

from app.models.ledger import Ledger
from app.services.extraction.party_matcher import match_party
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company


def _ledger(company, **overrides):  # type: ignore[no-untyped-def]
    base = {
        "company_id": company.id,
        "name": "Sharma Traders",
        "name_normalized": "sharma traders",
        "group_name": "Sundry Creditors",
    }
    base.update(overrides)
    return Ledger(**base)


def test_tier1_gstin_exact_match(db_session: Session) -> None:
    company = make_company(db_session)
    ledger = _ledger(company, gstin="27AAACR5055K1Z8")
    db_session.add(ledger)
    db_session.commit()

    result = match_party(
        db_session,
        company_id=company.id,
        vendor_gstin="27AAACR5055K1Z8",
        vendor_pan=None,
        vendor_name="Totally Different Name",
    )
    assert result.ledger_id == ledger.id
    assert result.flags == []


def test_tier2_pan_exact_match(db_session: Session) -> None:
    company = make_company(db_session)
    ledger = _ledger(company, pan="AAACR5055K")
    db_session.add(ledger)
    db_session.commit()

    result = match_party(
        db_session,
        company_id=company.id,
        vendor_gstin=None,
        vendor_pan="AAACR5055K",
        vendor_name="Some Other Name",
    )
    assert result.ledger_id == ledger.id
    assert result.flags == ["matched_via_pan_not_gstin"]


def test_tier3_fuzzy_name_match(db_session: Session) -> None:
    company = make_company(db_session)
    ledger = _ledger(company)
    db_session.add(ledger)
    db_session.commit()

    result = match_party(
        db_session,
        company_id=company.id,
        vendor_gstin=None,
        vendor_pan=None,
        vendor_name="Sharma Tradrs",  # typo
    )
    assert result.ledger_id == ledger.id
    assert result.flags == ["fuzzy_party_match"]


def test_tier4_no_match_flags_new_party(db_session: Session) -> None:
    company = make_company(db_session)
    result = match_party(
        db_session,
        company_id=company.id,
        vendor_gstin=None,
        vendor_pan=None,
        vendor_name="Nobody Has Heard Of This Vendor",
    )
    assert result.ledger_id is None
    assert result.flags == ["new_party"]


def test_inactive_ledger_not_matched(db_session: Session) -> None:
    company = make_company(db_session)
    ledger = _ledger(company, gstin="27AAACR5055K1Z8", is_active=False)
    db_session.add(ledger)
    db_session.commit()

    result = match_party(
        db_session,
        company_id=company.id,
        vendor_gstin="27AAACR5055K1Z8",
        vendor_pan=None,
        vendor_name=None,
    )
    assert result.ledger_id is None
    assert result.flags == ["new_party"]
