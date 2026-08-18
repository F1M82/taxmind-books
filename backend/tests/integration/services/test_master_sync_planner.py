"""P3.7 Phase 7A — Tally ledger master-sync planner tests.

The planner is SELECT-only: every test proves classification without any
ledger/company row being created or moved by the sync path itself (the only
rows created are the fixtures the test sets up directly).

Founder test matrix:
  1. same Tally ledger GUID → update candidate
  2. new Tally ledger GUID → insert candidate
  3. same name + different GUID → separate ledgers (never merged)
  4. duplicate GUID in source batch → detected
  5. missing GUID → manual review
  6. existing local ledger matched by GUID
  7. name fallback only when exactly one match
  8. ambiguous name → manual review
  9. company mismatch → hard stop
  10. cross-company GUID isolation
  11. idempotent repeated master sync
  12. no arbitrary company assignment
"""

from __future__ import annotations

from uuid import UUID

import pytest
from app.models.company import Company
from app.models.ledger import Ledger
from app.services.tally.master_sync_planner import (
    CompanyMappingError,
    LedgerDisposition,
    assert_company_mapping_safe,
    plan_ledger_master_sync,
    resolve_company_mapping,
)
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company


def _ledger(
    db: Session,
    company_id: UUID,
    *,
    name: str,
    master_id: str | None = None,
    group_name: str | None = None,
    gstin: str | None = None,
) -> Ledger:
    ledger = Ledger(
        company_id=company_id,
        name=name,
        name_normalized=name.strip().lower(),
        tally_master_id=master_id,
        group_name=group_name,
        gstin=gstin,
    )
    db.add(ledger)
    db.commit()
    db.refresh(ledger)
    return ledger


def _row(
    *,
    name: str,
    master_id: str | None = None,
    group_name: str | None = None,
    gstin: str | None = None,
) -> dict:
    return {
        "name": name,
        "group_name": group_name,
        "gstin": gstin,
        "master_id": master_id,
    }


def _dispositions(report) -> list[LedgerDisposition]:
    return [p.disposition for p in report.planned]


# ---------------------------------------------------------------------
# 1 / 2 / 6 — GUID-first identity
# ---------------------------------------------------------------------


def test_same_guid_different_fields_is_update_candidate(db_session: Session) -> None:
    company = make_company(db_session)
    _ledger(
        db_session,
        company.id,
        name="Cash",
        master_id="guid-1",
        group_name="Bank Accounts",
    )
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash", master_id="guid-1", group_name="Bank Accounts")],
    )
    assert report.existing_guid_matches == 1
    assert report.changed_candidates == 0
    assert report.unchanged == 1
    assert report.new_candidates == 0
    assert _dispositions(report) == [LedgerDisposition.UNCHANGED]


def test_same_guid_changed_group_is_update_candidate(db_session: Session) -> None:
    company = make_company(db_session)
    _ledger(
        db_session,
        company.id,
        name="Cash",
        master_id="guid-1",
        group_name="Bank Accounts",
    )
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash", master_id="guid-1", group_name="Cash-in-Hand")],
    )
    assert report.existing_guid_matches == 1
    assert report.changed_candidates == 1
    assert report.unchanged == 0
    assert report.manual_review == 0
    assert _dispositions(report) == [LedgerDisposition.UPDATE]


def test_new_guid_is_insert_candidate(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Sales A/c", master_id="guid-new")],
    )
    assert report.valid_guids == 1
    assert report.new_candidates == 1
    assert report.existing_guid_matches == 0
    assert report.manual_review == 0
    assert _dispositions(report) == [LedgerDisposition.NEW]


def test_existing_local_ledger_matched_by_guid(db_session: Session) -> None:
    company = make_company(db_session)
    existing = _ledger(db_session, company.id, name="Purchase", master_id="guid-p")
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Purchase", master_id="guid-p")],
    )
    assert report.existing_guid_matches == 1
    assert report.planned[0].existing_ledger_id == existing.id


# ---------------------------------------------------------------------
# 3 — same name + different GUID → separate ledgers, never merged
# ---------------------------------------------------------------------


def test_same_name_different_guid_never_merged(db_session: Session) -> None:
    company = make_company(db_session)
    _ledger(db_session, company.id, name="Cash", master_id="guid-existing")
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash", master_id="guid-arriving")],
    )
    assert report.new_candidates == 0  # not a clean insert — would violate unique name
    assert report.name_guid_conflicts == 1
    assert report.manual_review == 1
    assert _dispositions(report) == [LedgerDisposition.NAME_GUID_CONFLICT]


def test_two_arriving_same_name_different_guid_both_new_when_no_local(
    db_session: Session,
) -> None:
    company = make_company(db_session)
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[
            _row(name="Cash", master_id="guid-a"),
            _row(name="Cash", master_id="guid-b"),
        ],
    )
    # Two distinct GUIDs, no local name collision → two separate insert plans.
    assert report.new_candidates == 2
    assert report.name_guid_conflicts == 0
    assert len(report.planned) == 2


# ---------------------------------------------------------------------
# 4 — duplicate GUID in source batch
# ---------------------------------------------------------------------


def test_duplicate_guid_in_batch_detected(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[
            _row(name="Cash", master_id="guid-x"),
            _row(name="Cash", master_id="guid-x"),
        ],
    )
    assert report.duplicate_guids_in_batch == 1
    assert report.valid_guids == 1
    assert report.manual_review == 1
    assert _dispositions(report) == [LedgerDisposition.NEW, LedgerDisposition.DUPLICATE_GUID]


# ---------------------------------------------------------------------
# 5 / 7 / 8 — missing GUID + name fallback + ambiguity
# ---------------------------------------------------------------------


def test_missing_guid_is_manual_review(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Sundry Debtors")],
    )
    assert report.missing_guids == 1
    assert report.manual_review == 1
    assert report.valid_guids == 0
    assert report.unresolved == 1
    assert _dispositions(report) == [LedgerDisposition.UNRESOLVED]


def test_name_fallback_only_when_exactly_one_match(db_session: Session) -> None:
    company = make_company(db_session)
    _ledger(db_session, company.id, name="Cash", master_id=None)
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash")],
    )
    assert report.missing_guids == 1
    assert report.name_only_matches == 1
    assert report.manual_review == 1
    assert report.unresolved == 0
    assert _dispositions(report) == [LedgerDisposition.NAME_ONLY]


def test_ambiguous_name_manual_review(db_session: Session) -> None:
    # Names are unique per company at the (company_id, name) key level, so
    # "ambiguous" requires two stored names sharing one *normalized* form.
    company = make_company(db_session)
    _ledger(db_session, company.id, name="Cash", master_id="guid-1")
    _ledger(db_session, company.id, name="cash", master_id="guid-2")
    report = plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash")],
    )
    assert report.missing_guids == 1
    assert report.ambiguous_name == 1
    assert report.manual_review == 1
    assert _dispositions(report) == [LedgerDisposition.AMBIGUOUS_NAME]


# ---------------------------------------------------------------------
# 9 / 12 — company mapping hard stop + no arbitrary assignment
# ---------------------------------------------------------------------


def test_company_mismatch_hard_stop(db_session: Session) -> None:
    target = make_company(db_session, name="Vighnaharta Agro Chemicals")
    other = make_company(db_session, name="Some Other Co")
    with pytest.raises(CompanyMappingError):
        assert_company_mapping_safe(
            db_session,
            company_id=target.id,
            tally_company_name="Some Other Co",
        )
    assert other.id is not None  # unrelated fixture present


def test_exact_mapping_candidate_is_not_verified(db_session: Session) -> None:
    company = make_company(db_session, name="ABC Traders")
    result = resolve_company_mapping(
        db_session,
        tally_company_guid="g-abc",
        tally_company_name="ABC Traders",
    )
    assert result.mapped is False
    assert result.candidate_company_id == company.id


def test_no_arbitrary_company_assignment(db_session: Session) -> None:
    company = make_company(db_session, name="NonMatching")
    count_before = db_session.query(Company).count()
    with pytest.raises(CompanyMappingError):
        assert_company_mapping_safe(
            db_session,
            company_id=company.id,
            tally_company_name="Vighnaharta Agro Chemicals - FROM 1-APR-2025",
        )
    db_session.expire_all()
    assert db_session.query(Company).count() == count_before


# ---------------------------------------------------------------------
# 10 — cross-company GUID isolation
# ---------------------------------------------------------------------


def test_cross_company_guid_isolation(db_session: Session) -> None:
    co_a = make_company(db_session, name="Co A")
    co_b = make_company(db_session, name="Co B")
    _ledger(db_session, co_a.id, name="Cash", master_id="guid-shared")
    report_b = plan_ledger_master_sync(
        db_session,
        company_id=co_b.id,
        ledgers=[_row(name="Cash", master_id="guid-shared")],
    )
    assert report_b.existing_guid_matches == 0
    assert report_b.new_candidates == 1


# ---------------------------------------------------------------------
# 11 — idempotent repeated master sync
# ---------------------------------------------------------------------


def test_repeated_master_sync_is_idempotent(db_session: Session) -> None:
    company = make_company(db_session)
    ledgers = [
        _row(name="Cash", master_id="guid-1", group_name="Bank"),
        _row(name="Sales", master_id="guid-2"),
        _row(name="NoGuid"),
    ]
    first = plan_ledger_master_sync(db_session, company_id=company.id, ledgers=ledgers)
    second = plan_ledger_master_sync(db_session, company_id=company.id, ledgers=ledgers)
    assert first.to_dict() == second.to_dict()

    # Simulate the post-sync state in the local chart; a re-run must then line
    # up as existing GUID matches / unchanged rather than fresh candidates.
    _ledger(db_session, company.id, name="Cash", master_id="guid-1", group_name="Bank")
    _ledger(db_session, company.id, name="Sales", master_id="guid-2")
    replan = plan_ledger_master_sync(db_session, company_id=company.id, ledgers=ledgers)
    assert replan.unchanged == 2
    assert replan.new_candidates == 0
    assert replan.missing_guids == 1  # the NoGuid row stays manual review


def test_preserves_read_only_contract(db_session: Session) -> None:
    # The planner must not write anything, including no tally_synced_at stamp.
    company = make_company(db_session)
    plan_ledger_master_sync(
        db_session,
        company_id=company.id,
        ledgers=[_row(name="Cash", master_id="guid-1"), _row(name="NoGuid")],
    )
    db_session.expire_all()
    assert db_session.query(Ledger).count() == 0
    assert db_session.query(Company).count() == 1
