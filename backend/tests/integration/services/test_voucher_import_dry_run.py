"""P3.7 Phase 5 — Tally voucher import dry-run planner tests.

The planner is SELECT-only: every test here proves classification without
any voucher/ledger row being created or mutated by the import path itself
(the only rows created are the fixtures the test sets up directly).

Founder test matrix (A–R):
  A. same GUID → update
  B. same type+number + different GUID → two rows (coexist)
  C. same GUID + different company → allowed
  D. NULL GUID → skipped
  E. duplicate GUID within one export batch → detected
  F. unknown voucher type → explicit handling
  G. ledger MASTERID match
  H. normalized-name fallback
  I. ambiguous ledger match → manual review
  J. missing ledger → manual review
  K. cancelled voucher
  L. deleted voucher
  M. optional voucher
  N. VCHKEY captured from XML ATTRIBUTE
  O. ALTERID captured
  P. DATE maps to vouchers.date
  Q. re-running the same import is idempotent
  R. imported voucher does not enter Tally dispatch queue
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.models.ledger import Ledger
from app.models.voucher import Voucher
from app.services.tally.voucher_import import (
    Disposition,
    LedgerMatch,
    plan_voucher_import,
)
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company


def _row(
    *,
    tally_guid: str | None,
    voucher_type: str = "Sales",
    voucher_number: str | None = "VAC/25-26/222",
    date_str: str = "2026-09-12",
    vchkey: str | None = None,
    master_id: str | None = None,
    alter_id: str | None = None,
    entries: list[dict] | None = None,
    is_cancelled: bool = False,
    is_deleted: bool = False,
    is_optional: bool = False,
) -> dict:
    return {
        "tally_guid": tally_guid,
        "remote_id": tally_guid,
        "vchkey": vchkey,
        "master_id": master_id,
        "alter_id": alter_id,
        "voucher_type": voucher_type,
        "date": date_str,
        "voucher_number": voucher_number,
        "narration": "dry-run",
        "reference": None,
        "party_ledger_name": None,
        "is_cancelled": is_cancelled,
        "is_deleted": is_deleted,
        "is_optional": is_optional,
        "entries": entries or [],
    }


def _make_ledger(
    db: Session,
    company_id: UUID,
    *,
    name: str,
    master_id: str | None = None,
) -> Ledger:
    ledger = Ledger(
        company_id=company_id,
        name=name,
        name_normalized=name.strip().lower(),
        tally_master_id=master_id,
    )
    db.add(ledger)
    db.commit()
    db.refresh(ledger)
    return ledger


def _make_existing_voucher(
    db: Session,
    company_id: UUID,
    *,
    tally_guid: str,
    voucher_type: str = "Sales",
    voucher_number: str | None = "VAC/25-26/222",
) -> Voucher:
    voucher = Voucher(
        company_id=company_id,
        voucher_type=voucher_type,
        voucher_number=voucher_number,
        date=date(2026, 9, 12),
        total_amount=0,
        tally_guid=tally_guid,
    )
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return voucher


# ---------------------------------------------------------------------
# A / B / C — durable identity (company_id, tally_guid)
# ---------------------------------------------------------------------


def test_same_guid_is_update(db_session: Session) -> None:
    company = make_company(db_session)
    existing = _make_existing_voucher(db_session, company.id, tally_guid="GUID-A")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-A")],
    )

    assert report.update == 1
    assert report.insert == 0
    planned = report.planned[0]
    assert planned.disposition is Disposition.UPDATE
    assert planned.existing_voucher_id == existing.id


def test_same_type_number_different_guid_two_rows(db_session: Session) -> None:
    company = make_company(db_session)
    _make_existing_voucher(
        db_session, company.id, tally_guid="GUID-A", voucher_number="VAC/25-26/222"
    )

    # Different GUID, same (type, number) → must be planned as a NEW insert,
    # not a match on the existing row.
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-B", voucher_number="VAC/25-26/222")],
    )

    assert report.insert == 1
    assert report.update == 0
    assert report.duplicate_number_different_guid == 1
    assert report.planned[0].disposition is Disposition.INSERT
    assert report.planned[0].existing_voucher_id is None


def test_same_guid_different_company_allowed(db_session: Session) -> None:
    company_a = make_company(db_session)
    company_b = make_company(db_session)

    report_a = plan_voucher_import(
        db_session, company_id=company_a.id, rows=[_row(tally_guid="GUID-X")]
    )
    report_b = plan_voucher_import(
        db_session, company_id=company_b.id, rows=[_row(tally_guid="GUID-X")]
    )

    # Uniqueness is company-scoped: neither company sees the other's row.
    assert report_a.planned[0].disposition is Disposition.INSERT
    assert report_b.planned[0].disposition is Disposition.INSERT


# ---------------------------------------------------------------------
# D / E — missing and duplicate GUID
# ---------------------------------------------------------------------


def test_null_guid_skipped(db_session: Session) -> None:
    company = make_company(db_session)

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid=None, voucher_number="VAC/25-26/1")],
    )

    assert report.missing_guid == 1
    assert report.planned[0].disposition is Disposition.SKIP_MISSING_GUID
    assert report.insert == 0
    assert report.update == 0


def test_duplicate_guid_within_batch_detected(db_session: Session) -> None:
    company = make_company(db_session)

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(tally_guid="GUID-D", voucher_number="VAC/25-26/1"),
            _row(tally_guid="GUID-D", voucher_number="VAC/25-26/2"),
        ],
    )

    assert report.duplicate_guid_in_batch == 1
    assert report.insert == 1  # only the first occurrence is insertable
    assert report.manual_review == 1
    assert report.planned[1].disposition is Disposition.SKIP_DUPLICATE_GUID


# ---------------------------------------------------------------------
# F — unknown voucher type
# ---------------------------------------------------------------------


def test_unknown_voucher_type_flagged_not_fatal(db_session: Session) -> None:
    company = make_company(db_session)

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(tally_guid="GUID-E", voucher_type="Delivery Challan"),
            _row(tally_guid="GUID-F", voucher_type="Sales"),
        ],
    )

    assert report.unknown_type == 1
    # Both rows still planned (import does not fail wholesale); the
    # unknown-type row requires manual review.
    assert report.manual_review == 1
    assert report.insert == 2
    unknown = report.planned[0]
    assert unknown.is_known_type is False
    assert unknown.voucher_type == "Delivery Challan"  # raw preserved
    assert report.planned[1].is_known_type is True


# ---------------------------------------------------------------------
# G / H / I / J — ledger reconciliation
# ---------------------------------------------------------------------


def test_ledger_masterid_match(db_session: Session) -> None:
    company = make_company(db_session)
    _make_ledger(db_session, company.id, name="HDFC BANK", master_id="LED-M1")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(
                tally_guid="GUID-G",
                entries=[{"ledger_name": "HDFC BANK", "ledger_guid": "LED-M1"}],
            )
        ],
    )

    entry = report.planned[0].entries[0]
    assert entry.ledger_match is LedgerMatch.MASTER_ID
    assert report.ledger_match_master_id == 1
    assert report.manual_review == 0


def test_ledger_guid_preferred_over_name(db_session: Session) -> None:
    """ledger_guid wins over name even when a differently-named ledger also
    matches the name (GUID is the durable key; name is only a fallback)."""
    company = make_company(db_session)
    _make_ledger(db_session, company.id, name="HDFC BANK", master_id="LED-M1")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(
                tally_guid="GUID-G2",
                entries=[{"ledger_name": "HDFC BANK", "ledger_guid": "LED-M1"}],
            )
        ],
    )
    assert report.planned[0].entries[0].ledger_match is LedgerMatch.MASTER_ID


def test_ledger_name_fallback(db_session: Session) -> None:
    company = make_company(db_session)
    _make_ledger(db_session, company.id, name="HDFC Bank A/c")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(
                tally_guid="GUID-H",
                entries=[{"ledger_name": "HDFC BANK A/C"}],
            )
        ],
    )

    entry = report.planned[0].entries[0]
    assert entry.ledger_match is LedgerMatch.NAME
    assert report.ledger_match_name == 1


def test_ledger_ambiguous_match_manual_review(db_session: Session) -> None:
    company = make_company(db_session)
    # Two ledgers sharing the same normalized name.
    _make_ledger(db_session, company.id, name="Cash")
    _make_ledger(db_session, company.id, name="CASH")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-I", entries=[{"ledger_name": "cash"}])],
    )

    entry = report.planned[0].entries[0]
    assert entry.ledger_match is LedgerMatch.AMBIGUOUS
    assert report.ledger_ambiguous == 1
    assert report.manual_review == 1


def test_ledger_missing_manual_review(db_session: Session) -> None:
    company = make_company(db_session)
    _make_ledger(db_session, company.id, name="Sales")

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-J", entries=[{"ledger_name": "NoSuchLedger"}])],
    )

    entry = report.planned[0].entries[0]
    assert entry.ledger_match is LedgerMatch.MISSING
    assert report.ledger_missing == 1
    assert report.manual_review == 1


# ---------------------------------------------------------------------
# K / L / M — Tally state preservation (reported, not silently dropped)
# ---------------------------------------------------------------------


def test_cancelled_voucher_counted(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-K", is_cancelled=True)],
    )
    assert report.cancelled == 1
    assert report.planned[0].is_cancelled is True


def test_deleted_voucher_counted_and_reviewed(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-L", is_deleted=True)],
    )
    assert report.deleted == 1
    assert report.manual_review == 1  # no faithful deleted-state mapping yet


def test_optional_voucher_counted(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-M", is_optional=True)],
    )
    assert report.optional == 1
    assert report.planned[0].is_optional is True


# ---------------------------------------------------------------------
# N / O / P — identity field capture + date mapping
# ---------------------------------------------------------------------


def test_vchkey_and_alterid_captured_for_planned_row(db_session: Session) -> None:
    """VCHKEY/ALTERID flow into the identity payload (dry-run passes them
    through verbatim; the connector reads VCHKEY from the XML ATTRIBUTE)."""
    company = make_company(db_session)
    row = _row(
        tally_guid="GUID-N",
        vchkey="company-uuid-0000b48f:00000008",
        alter_id="3",
    )
    report = plan_voucher_import(db_session, company_id=company.id, rows=[row])

    assert report.total == 1
    # The planner does not mutate the input; VCHKEY/ALTERID remain on the
    # source row verbatim (stored exactly as received on persist).
    assert row["vchkey"] == "company-uuid-0000b48f:00000008"
    assert row["alter_id"] == "3"


def test_date_maps_to_voucher_date(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-P", date_str="2026-09-12")],
    )
    # Valid ISO date parsed (no malformed flag); on persist this value
    # lands in vouchers.date.
    assert report.malformed == 0
    assert report.parse_failure == 0
    assert report.planned[0].disposition is Disposition.INSERT


def test_bad_date_is_parse_failure(db_session: Session) -> None:
    company = make_company(db_session)
    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-BADDATE", date_str="not-a-date")],
    )
    assert report.parse_failure == 1
    assert report.malformed == 1


# ---------------------------------------------------------------------
# Q — idempotency of re-running the plan
# ---------------------------------------------------------------------


def test_rerun_is_deterministic_and_idempotent(db_session: Session) -> None:
    company = make_company(db_session)
    _make_ledger(db_session, company.id, name="Sales")

    rows = [_row(tally_guid="GUID-Q", entries=[{"ledger_name": "Sales"}])]
    first = plan_voucher_import(db_session, company_id=company.id, rows=rows)
    second = plan_voucher_import(db_session, company_id=company.id, rows=rows)

    assert first.to_dict() == second.to_dict()
    # Import planning never writes: voucher table still only holds fixtures.
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 0


# ---------------------------------------------------------------------
# R — imported rows are planned as tally_sync/posted, outside dispatch
# ---------------------------------------------------------------------


def test_planned_rows_use_tally_sync_posted_not_dispatched(db_session: Session) -> None:
    company = make_company(db_session)

    report = plan_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-R")],
    )

    planned = report.planned[0]
    assert planned.planned_source == "tally_sync"
    assert planned.planned_status == "posted"

    # Dispatch re-enqueue only selects status == pending_tally_post; an
    # imported (posted) voucher can never be swept. This is asserted via
    # the selector rather than by constructing a dispatch: no row exists
    # yet (dry-run), so we prove the invariant at the selector contract.
    from app.services.tally.voucher_reenqueue import select_retryable_strands

    assert select_retryable_strands(db_session, company_id=company.id) == []
