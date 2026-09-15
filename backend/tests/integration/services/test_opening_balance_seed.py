"""P3.2 — opening-balance seed operation tests.

Covers docs/PHASE_3_OPENING_BALANCE_ARCHITECTURE.md's correctness
requirements: seed once from a Tally Trial Balance, never re-write on a
same-anchor re-run, never overwrite a value the seed didn't itself write,
lock the anchor date on first run, and fail closed on company identity
(the same gate sync_masters uses).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.audit import AuditContext, AuditEmitter
from app.core.exceptions import OpeningBalanceAnchorMismatch
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.ledger import BalanceType, Ledger
from app.services.tally.company_mapping import CompanyMappingError
from app.services.tally.opening_balance_seed import seed_opening_balances
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company

GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"
ANCHOR = date(2023, 4, 1)


def _audit(db: Session, company: Company) -> AuditEmitter:
    return AuditEmitter(
        db,
        AuditContext(
            company=company,
            user=None,
            ip_address=None,
            user_agent="test/1.0",
            request_id=uuid4(),
            source="connector",
        ),
    )


def _make_ledger(
    db: Session,
    company: Company,
    *,
    name: str,
    tally_master_id: str | None,
    opening_balance: Decimal = Decimal("0"),
    balance_type: BalanceType = BalanceType.Dr,
    opening_balance_seeded_at=None,
) -> Ledger:
    ledger = Ledger(
        company_id=company.id,
        name=name,
        name_normalized=name.strip().lower(),
        opening_balance=opening_balance,
        balance_type=balance_type,
        tally_master_id=tally_master_id,
        opening_balance_seeded_at=opening_balance_seeded_at,
    )
    db.add(ledger)
    db.commit()
    db.refresh(ledger)
    return ledger


def _mapped_company(db: Session) -> Company:
    return make_company(db, tally_master_id=GUID)


# ---------------------------------------------------------------------
# Sign mapping + happy path
# ---------------------------------------------------------------------


def test_seeds_positive_closing_balance_as_dr(db_session: Session) -> None:
    company = _mapped_company(db_session)
    ledger = _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "50000.00"}],
    )
    db_session.commit()

    assert result.seeded == 1
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("50000.00")
    assert ledger.balance_type == BalanceType.Dr
    assert ledger.opening_balance_seeded_at is not None


def test_seeds_negative_closing_balance_as_cr(db_session: Session) -> None:
    company = _mapped_company(db_session)
    ledger = _make_ledger(db_session, company, name="Sales", tally_master_id="tid-2")

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Sales", "closing_balance": "-100000.00"}],
    )
    db_session.commit()

    assert result.seeded == 1
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("100000.00")
    assert ledger.balance_type == BalanceType.Cr


def test_seed_writes_the_documented_audit_action(db_session: Session) -> None:
    company = _mapped_company(db_session)
    _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "1234.56"}],
    )
    db_session.commit()

    rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "ledger.opening_balance_seeded")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].new_value["anchor_date"] == ANCHOR.isoformat()


def test_company_anchor_locked_on_first_run(db_session: Session) -> None:
    company = _mapped_company(db_session)
    _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "1.00"}],
    )
    db_session.commit()
    db_session.refresh(company)
    assert company.opening_balance_anchor_date == ANCHOR
    assert company.opening_balance_seeded_at is not None


# ---------------------------------------------------------------------
# Idempotency: same-anchor re-run is a no-op
# ---------------------------------------------------------------------


def test_rerun_with_same_anchor_does_not_rewrite(db_session: Session) -> None:
    company = _mapped_company(db_session)
    ledger = _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "50000.00"}],
    )
    db_session.commit()

    # Second run: Tally now (hypothetically) reports a different number --
    # the already-seeded ledger must NOT move.
    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "99999.00"}],
    )
    db_session.commit()

    assert result.seeded == 0
    assert result.skipped_already_seeded == 1
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("50000.00")

    audit_count = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "ledger.opening_balance_seeded")
        .count()
    )
    assert audit_count == 1


def test_different_anchor_on_second_run_is_refused(db_session: Session) -> None:
    company = _mapped_company(db_session)
    _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "1.00"}],
    )
    db_session.commit()

    with pytest.raises(OpeningBalanceAnchorMismatch):
        seed_opening_balances(
            db_session,
            _audit(db_session, company),
            company_id=company.id,
            anchor_date=date(2024, 4, 1),
            tally_company_guid=GUID,
            rows=[{"name": "Cash", "closing_balance": "1.00"}],
        )
    db_session.rollback()


# ---------------------------------------------------------------------
# Never overwrite a value the seed didn't write
# ---------------------------------------------------------------------


def test_manual_opening_balance_is_never_overwritten(db_session: Session) -> None:
    # A Tally-synced ledger whose opening_balance was set some other way
    # (e.g. a direct PATCH) and never stamped opening_balance_seeded_at.
    company = _mapped_company(db_session)
    ledger = _make_ledger(
        db_session,
        company,
        name="HDFC Bank",
        tally_master_id="tid-3",
        opening_balance=Decimal("777.00"),
        balance_type=BalanceType.Dr,
    )

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "HDFC Bank", "closing_balance": "999.00"}],
    )
    db_session.commit()

    assert result.seeded == 0
    assert result.skipped_conflict == ["HDFC Bank"]
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("777.00")
    assert ledger.opening_balance_seeded_at is None


def test_ledger_not_tally_synced_is_skipped(db_session: Session) -> None:
    # Phase A direct-entry ledger sharing a name with a Tally row --
    # must not receive a Tally-sourced opening.
    company = _mapped_company(db_session)
    ledger = _make_ledger(db_session, company, name="Petty Cash", tally_master_id=None)

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Petty Cash", "closing_balance": "500.00"}],
    )
    db_session.commit()

    assert result.seeded == 0
    assert result.skipped_not_tally_synced == 1
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("0")


def test_unmatched_tally_row_is_reported_not_errored(db_session: Session) -> None:
    company = _mapped_company(db_session)
    _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[
            {"name": "Cash", "closing_balance": "1.00"},
            {"name": "Unknown Ledger Not In TaxMind", "closing_balance": "2.00"},
        ],
    )
    db_session.commit()

    assert result.seeded == 1
    assert result.unmatched_tally_rows == ["Unknown Ledger Not In TaxMind"]


def test_malformed_closing_balance_is_reported_not_errored(
    db_session: Session,
) -> None:
    company = _mapped_company(db_session)
    ledger = _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    result = seed_opening_balances(
        db_session,
        _audit(db_session, company),
        company_id=company.id,
        anchor_date=ANCHOR,
        tally_company_guid=GUID,
        rows=[{"name": "Cash", "closing_balance": "not-a-number"}],
    )
    db_session.commit()

    assert result.seeded == 0
    assert result.malformed_rows == ["Cash"]
    db_session.refresh(ledger)
    assert ledger.opening_balance_seeded_at is None


# ---------------------------------------------------------------------
# Fail-closed company identity gate
# ---------------------------------------------------------------------


def test_unmapped_company_refuses_to_write(db_session: Session) -> None:
    company = make_company(db_session)  # no tally_master_id
    ledger = _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    with pytest.raises(CompanyMappingError):
        seed_opening_balances(
            db_session,
            _audit(db_session, company),
            company_id=company.id,
            anchor_date=ANCHOR,
            tally_company_guid=GUID,
            rows=[{"name": "Cash", "closing_balance": "1.00"}],
        )
    db_session.rollback()
    db_session.refresh(ledger)
    assert ledger.opening_balance == Decimal("0")
    assert ledger.opening_balance_seeded_at is None


def test_mismatched_guid_refuses_to_write(db_session: Session) -> None:
    company = _mapped_company(db_session)
    _make_ledger(db_session, company, name="Cash", tally_master_id="tid-1")

    with pytest.raises(CompanyMappingError):
        seed_opening_balances(
            db_session,
            _audit(db_session, company),
            company_id=company.id,
            anchor_date=ANCHOR,
            tally_company_guid="some-other-guid",
            rows=[{"name": "Cash", "closing_balance": "1.00"}],
        )
    db_session.rollback()
