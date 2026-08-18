"""P3.7 Phase 6B — Tally voucher import persistence tests.

Exercises the WRITE path (`persist_voucher_import` → `VoucherService
.upsert_from_tally`) against a real Postgres. The dry-run planner
(`plan_voucher_import`) is READ-ONLY; these tests prove persistence is
correct, atomic, and idempotent under the durable identity
``(company_id, tally_guid)``.

Mandatory test matrix (founder Phase 6B §12):
  1. new GUID → INSERT
  2. same GUID → UPDATE
  3. same type+number+different GUID → two rows
  4. same GUID + different company → allowed
  5. NULL GUID → skipped
  6. unknown type → voucher_type NULL + raw preserved
  7. known type → enum + raw preserved
  8. ISDELETED → flag set, status unchanged, manual review
  9. ISCANCELLED → flag set, status unchanged
  10. ISOPTIONAL → flag set, is_optional_in_tally independent
  11. ledger GUID exact match
  12. ledger name fallback
  13. ambiguous ledger → manual review
  14. missing ledger → manual review
  15. VCHKEY persisted
  16. ALTERID persisted
  17. date persisted
  18. re-import idempotent
  19. changed ALTERID/VCHKEY → same row updated
  20. source/status cannot enter dispatch
  21. duplicate GUID in batch detected
  22. atomic rollback on failure
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from app.core.audit import AuditContext, AuditEmitter
from app.models.ledger import Ledger
from app.models.voucher import Voucher, VoucherStatus, VoucherType
from app.services.tally.voucher_import import persist_voucher_import
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company, make_user


def _audit(db: Session, company, user) -> AuditEmitter:  # type: ignore[no-untyped-def]
    return AuditEmitter(
        db,
        AuditContext(
            company=company,
            user=user,
            ip_address=None,
            user_agent="test/1.0",
            request_id=uuid4(),
            source="connector",
        ),
    )


def _row(
    *,
    tally_guid: str | None,
    voucher_type: str = "Sales",
    voucher_number: str | None = "VAC/25-26/222",
    date_str: str = "2026-09-12",
    narration: str | None = None,
    reference: str | None = None,
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
        "narration": narration,
        "reference": reference,
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


def _two_line_entries(bank: Ledger, party: Ledger, amount: str = "100.00") -> list[dict]:
    return [
        {"ledger_name": bank.name, "ledger_guid": bank.tally_master_id,
         "amount": amount, "entry_type": "Dr"},
        {"ledger_name": party.name, "ledger_guid": party.tally_master_id,
         "amount": f"-{amount}", "entry_type": "Cr"},
    ]


def _setup(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db, name="Acme")
    bank = _make_ledger(db, company.id, name="Bank", master_id="LED-BANK")
    party = _make_ledger(db, company.id, name="Sharma", master_id="LED-SHARMA")
    return user, company, bank, party


# ---------------------------------------------------------------------
# 1 / 2 / 18 / 19 — INSERT, UPDATE, idempotency
# ---------------------------------------------------------------------


def test_new_guid_inserts(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-1", entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.inserted == 1
    assert report.updated == 0
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-1")
        .one()
    )
    assert v.source == "tally_sync"
    assert v.status == VoucherStatus.posted
    assert len(v.entries) == 2


def test_same_guid_updates(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    first = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-1", entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    second = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-1", entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert first.inserted == 1
    assert second.updated == 1
    assert second.inserted == 0
    count = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-1")
        .count()
    )
    assert count == 1


def test_changed_alterid_vchkey_updates_same_row(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-1", vchkey="v1", alter_id="1",
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-1", vchkey="v2", alter_id="2",
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.updated == 1
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-1")
        .one()
    )
    assert v.tally_vchkey == "v2"
    assert v.tally_alter_id == "2"


# ---------------------------------------------------------------------
# 3 — same type+number, different GUID → two rows
# ---------------------------------------------------------------------


def test_same_type_number_different_guid_two_rows(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    rows = [
        _row(tally_guid="GUID-A", voucher_number="VAC/25-26/222",
             entries=_two_line_entries(bank, party)),
        _row(tally_guid="GUID-B", voucher_number="VAC/25-26/222",
             entries=_two_line_entries(bank, party)),
    ]
    report = persist_voucher_import(
        db_session, company_id=company.id, rows=rows,
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.inserted == 2
    count = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id)
        .count()
    )
    assert count == 2


# ---------------------------------------------------------------------
# 4 — same GUID different company
# ---------------------------------------------------------------------


def test_same_guid_different_company_allowed(db_session: Session) -> None:
    user, company_a, bank_a, party_a = _setup(db_session)
    company_b = make_company(db_session, name="Beta")
    bank_b = _make_ledger(db_session, company_b.id, name="Bank", master_id="LED-BANK-B")
    party_b = _make_ledger(db_session, company_b.id, name="Sharma", master_id="LED-SHARMA-B")

    persist_voucher_import(
        db_session, company_id=company_a.id,
        rows=[_row(tally_guid="GUID-X", entries=_two_line_entries(bank_a, party_a))],
        audit=_audit(db_session, company_a, user),
    )
    report = persist_voucher_import(
        db_session, company_id=company_b.id,
        rows=[_row(tally_guid="GUID-X", entries=_two_line_entries(bank_b, party_b))],
        audit=_audit(db_session, company_b, user),
    )
    db_session.commit()
    assert report.inserted == 1  # separate company → separate row


# ---------------------------------------------------------------------
# 5 — NULL GUID skipped
# ---------------------------------------------------------------------


def test_null_guid_skipped(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid=None, entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.skipped_missing_guid == 1
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 0


# ---------------------------------------------------------------------
# 6 / 7 — known vs unknown voucher type
# ---------------------------------------------------------------------


def test_known_type_enum_plus_raw_preserved(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-K", voucher_type="Sales",
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-K")
        .one()
    )
    assert v.voucher_type == VoucherType.Sales
    assert v.tally_voucher_type == "Sales"


def test_unknown_type_null_enum_raw_preserved(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-U", voucher_type="Delivery Challan",
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-U")
        .one()
    )
    assert v.voucher_type is None
    assert v.tally_voucher_type == "Delivery Challan"


# ---------------------------------------------------------------------
# 8 / 9 / 10 — Tally origin flags
# ---------------------------------------------------------------------


def test_isdeleted_routed_to_manual_review_not_persisted(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-D", is_deleted=True,
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.manual_review == 1
    assert report.inserted == 0
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 0


def test_iscancelled_flag_persisted_status_unchanged(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-C", is_cancelled=True,
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-C")
        .one()
    )
    assert v.tally_is_cancelled is True
    assert v.status == VoucherStatus.posted  # NOT cancelled


def test_isoptional_flag_independent_of_is_optional_in_tally(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-O", is_optional=True,
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-O")
        .one()
    )
    assert v.tally_is_optional is True
    assert v.is_optional_in_tally is False  # independent TaxMind field


# ---------------------------------------------------------------------
# 11 / 12 / 13 / 14 — ledger reconciliation
# ---------------------------------------------------------------------


def test_ledger_guid_exact_match(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-L1", entries=[
            {"ledger_name": "Wrong Name", "ledger_guid": "LED-BANK",
             "amount": "100.00", "entry_type": "Dr"},
            {"ledger_name": "Wrong Name", "ledger_guid": "LED-SHARMA",
             "amount": "-100.00", "entry_type": "Cr"},
        ])],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.inserted == 1  # GUID matched despite wrong names


def test_ledger_name_fallback(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-L2", entries=[
            {"ledger_name": "bank", "amount": "100.00", "entry_type": "Dr"},
            {"ledger_name": "sharma", "amount": "-100.00", "entry_type": "Cr"},
        ])],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.inserted == 1


def test_ambiguous_ledger_manual_review(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    _make_ledger(db_session, company.id, name="BANK")  # collides normalized name
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-L3", entries=[
            {"ledger_name": "bank", "amount": "100.00", "entry_type": "Dr"},
            {"ledger_name": "sharma", "amount": "-100.00", "entry_type": "Cr"},
        ])],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.manual_review == 1
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 0


def test_missing_ledger_manual_review(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-L4", entries=[
            {"ledger_name": "NoSuchLedger", "amount": "100.00", "entry_type": "Dr"},
            {"ledger_name": "sharma", "amount": "-100.00", "entry_type": "Cr"},
        ])],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.manual_review == 1
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 0


# ---------------------------------------------------------------------
# 15 / 16 / 17 — identity metadata + date
# ---------------------------------------------------------------------


def test_vchkey_alterid_date_persisted(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-M", vchkey="guid:1", alter_id="3",
                   master_id="9001", date_str="2026-09-12",
                   entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    v = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id, Voucher.tally_guid == "GUID-M")
        .one()
    )
    assert v.tally_vchkey == "guid:1"
    assert v.tally_alter_id == "3"
    assert v.tally_master_id == "9001"
    assert v.date == date(2026, 9, 12)


# ---------------------------------------------------------------------
# 20 — imported vouchers cannot enter dispatch
# ---------------------------------------------------------------------


def test_imported_voucher_not_dispatchable(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[_row(tally_guid="GUID-DISP", entries=_two_line_entries(bank, party))],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    from app.services.tally.voucher_reenqueue import select_retryable_strands

    strands = select_retryable_strands(db_session, company_id=company.id)
    assert strands == []  # posted/tally_sync never swept


# ---------------------------------------------------------------------
# 21 — duplicate GUID in batch
# ---------------------------------------------------------------------


def test_duplicate_guid_in_batch_detected(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    report = persist_voucher_import(
        db_session,
        company_id=company.id,
        rows=[
            _row(tally_guid="GUID-DUP", entries=_two_line_entries(bank, party)),
            _row(tally_guid="GUID-DUP", entries=_two_line_entries(bank, party)),
        ],
        audit=_audit(db_session, company, user),
    )
    db_session.commit()
    assert report.skipped_duplicate_guid_in_batch == 1
    assert report.inserted == 1
    assert db_session.query(Voucher).filter(Voucher.company_id == company.id).count() == 1


# ---------------------------------------------------------------------
# 22 — atomic rollback on failure
# ---------------------------------------------------------------------


def test_atomic_rollback_on_failure(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    # First voucher is valid; the second passes reconciliation but has a
    # zero amount, which violates LedgerEntry.amount > 0 at flush time.
    # The whole batch must roll back — no partial voucher survives.
    rows = [
        _row(tally_guid="GUID-OK", entries=_two_line_entries(bank, party)),
        _row(
            tally_guid="GUID-BAD",
            entries=[
                {"ledger_name": bank.name, "ledger_guid": bank.tally_master_id,
                 "amount": "0", "entry_type": "Dr"},
                {"ledger_name": party.name, "ledger_guid": party.tally_master_id,
                 "amount": "0", "entry_type": "Cr"},
            ],
        ),
    ]
    with pytest.raises(StatementError):
        persist_voucher_import(
            db_session,
            company_id=company.id,
            rows=rows,
            audit=_audit(db_session, company, user),
        )
        db_session.commit()
    db_session.rollback()
    assert (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id)
        .count()
        == 0
    )
