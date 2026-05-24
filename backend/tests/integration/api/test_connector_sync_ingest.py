"""Integration tests for sync_masters → ledger ingest (P0.46b).

Covers the persistence path that the connector.py `_drive()` background
task invokes after `send_command` returns `status=success`. The full
WebSocket → command → reply loop is exercised in test_connector_sync.py;
here we test the persistence helper directly because the background
asyncio task is hard to await deterministically through TestClient.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import pytest
from app.api.v1.connector import persist_sync_masters_payload
from app.core.audit import AuditContext, AuditEmitter
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.services.ledger_service import LedgerService
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    make_company,
    make_membership,
    make_user,
)

# ---------------------------------------------------------------------
# Service-level: LedgerService.upsert_from_sync
# ---------------------------------------------------------------------


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


def _sample_ledgers() -> list[dict[str, object]]:
    return [
        {
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "gstin": None,
            "master_id": "tally-sharma-guid",
        },
        {"name": "HDFC Bank A/c", "group_name": "Bank Accounts", "gstin": None},
        {"name": "Sales", "group_name": "Sales Accounts", "gstin": None},
    ]


def test_upsert_from_sync_creates_rows_under_correct_tenant(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    counts = service.upsert_from_sync(ledgers=_sample_ledgers(), groups=[])
    db_session.commit()

    assert counts == {"created": 3, "updated": 0, "skipped": 0}

    rows = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id)
        .order_by(Ledger.name_normalized)
        .all()
    )
    assert [r.name for r in rows] == ["HDFC Bank A/c", "Sales", "Sharma Traders"]
    assert all(r.is_active for r in rows)
    assert {r.name: r.group_name for r in rows} == {
        "HDFC Bank A/c": "Bank Accounts",
        "Sales": "Sales Accounts",
        "Sharma Traders": "Sundry Debtors",
    }

    audits = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.created",
        )
        .all()
    )
    assert len(audits) == 3
    assert all(a.source == "connector" for a in audits)

    # BUG-005 step 2e: tally_master_id persists from payload (and None
    # passes through when absent); tally_synced_at is stamped on every
    # processed row.
    by_name = {r.name: r for r in rows}
    assert by_name["Sharma Traders"].tally_master_id == "tally-sharma-guid"
    assert by_name["HDFC Bank A/c"].tally_master_id is None
    assert by_name["Sales"].tally_master_id is None
    assert all(r.tally_synced_at is not None for r in rows)
    sharma_audit = next(
        a for a in audits if a.entity_id == by_name["Sharma Traders"].id
    )
    assert sharma_audit.new_value["tally_master_id"] == "tally-sharma-guid"


def test_upsert_from_sync_is_idempotent(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    service.upsert_from_sync(ledgers=_sample_ledgers(), groups=[])
    db_session.commit()

    # Re-run with identical payload — should be a no-op.
    counts = service.upsert_from_sync(ledgers=_sample_ledgers(), groups=[])
    db_session.commit()
    assert counts == {"created": 0, "updated": 0, "skipped": 0}

    # Still only 3 ledgers; no extra audit rows beyond the original 3 creates.
    assert (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id)
        .count()
        == 3
    )
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action.in_(("ledger.created", "ledger.updated")),
        )
        .count()
        == 3
    )


def test_upsert_from_sync_updates_changed_fields(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    service.upsert_from_sync(ledgers=_sample_ledgers(), groups=[])
    db_session.commit()

    changed = [
        {"name": "Sharma Traders", "group_name": "Sundry Creditors", "gstin": None},
        {"name": "HDFC Bank A/c", "group_name": "Bank Accounts", "gstin": None},
        {"name": "Sales", "group_name": "Sales Accounts", "gstin": None},
    ]
    counts = service.upsert_from_sync(ledgers=changed, groups=[])
    db_session.commit()

    assert counts == {"created": 0, "updated": 1, "skipped": 0}
    row = (
        db_session.query(Ledger)
        .filter(
            Ledger.company_id == company.id,
            Ledger.name == "Sharma Traders",
        )
        .one()
    )
    assert row.group_name == "Sundry Creditors"


def test_upsert_from_sync_reactivates_soft_deleted(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Debtors"}],
        groups=[],
    )
    db_session.commit()

    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    row.is_active = False
    db_session.commit()

    counts = service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Debtors"}],
        groups=[],
    )
    db_session.commit()
    assert counts["updated"] == 1
    db_session.refresh(row)
    assert row.is_active is True


def test_upsert_from_sync_skips_invalid_rows(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    counts = service.upsert_from_sync(
        ledgers=[
            {"name": "Valid Ledger", "group_name": None},
            {"name": "", "group_name": None},          # empty
            {"name": "   ", "group_name": None},       # whitespace
            {"group_name": "no-name"},                  # missing name
            "not-a-dict",                               # type-bad row
        ],
        groups=[],
    )
    db_session.commit()
    assert counts == {"created": 1, "updated": 0, "skipped": 4}


# ---------------------------------------------------------------------
# BUG-005 step 2e: tally_master_id reconciliation matrix
# ---------------------------------------------------------------------


def test_upsert_from_sync_reconciles_null_local_when_payload_brings_guid(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(
        db_session, _audit(db_session, company, user), company.id
    )
    # Seed: an unsynced legacy ledger (no master_id in payload).
    service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Debtors"}],
        groups=[],
    )
    db_session.commit()

    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    assert row.tally_master_id is None  # precondition

    # Re-sync: payload now carries the GUID — main reconciliation path.
    counts = service.upsert_from_sync(
        ledgers=[{
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "master_id": "tally-sharma-guid",
        }],
        groups=[],
    )
    db_session.commit()

    assert counts == {"created": 0, "updated": 1, "skipped": 0}
    db_session.refresh(row)
    assert row.tally_master_id == "tally-sharma-guid"
    assert row.tally_synced_at is not None

    # Audit row reflects the NULL→GUID transition.
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.updated",
            AuditLog.entity_id == row.id,
        )
        .one()
    )
    assert audit.old_value["tally_master_id"] is None
    assert audit.new_value["tally_master_id"] == "tally-sharma-guid"


def test_upsert_from_sync_preserves_local_guid_when_payload_is_null(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(
        db_session, _audit(db_session, company, user), company.id
    )
    # Seed with a known-good GUID.
    service.upsert_from_sync(
        ledgers=[{
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "master_id": "known-guid",
        }],
        groups=[],
    )
    db_session.commit()

    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    assert row.tally_master_id == "known-guid"

    # Re-sync with no master_id in payload — must not clobber.
    counts = service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Debtors"}],
        groups=[],
    )
    db_session.commit()

    assert counts == {"created": 0, "updated": 0, "skipped": 0}
    db_session.refresh(row)
    assert row.tally_master_id == "known-guid"  # preserved
    assert row.tally_synced_at is not None  # stamped even on no-op

    # No ledger.updated audit row — nothing changed semantically.
    audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.updated",
            AuditLog.entity_id == row.id,
        )
        .count()
    )
    assert audit_count == 0


def test_upsert_from_sync_no_op_when_guids_match(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(
        db_session, _audit(db_session, company, user), company.id
    )
    service.upsert_from_sync(
        ledgers=[{
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "master_id": "matching-guid",
        }],
        groups=[],
    )
    db_session.commit()

    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    assert row.tally_master_id == "matching-guid"

    # Re-sync with identical GUID — matrix fall-through.
    counts = service.upsert_from_sync(
        ledgers=[{
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "master_id": "matching-guid",
        }],
        groups=[],
    )
    db_session.commit()

    assert counts == {"created": 0, "updated": 0, "skipped": 0}
    db_session.refresh(row)
    assert row.tally_master_id == "matching-guid"
    assert row.tally_synced_at is not None

    audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.updated",
            AuditLog.entity_id == row.id,
        )
        .count()
    )
    assert audit_count == 0


def test_upsert_from_sync_logs_warning_on_guid_mismatch(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(
        db_session, _audit(db_session, company, user), company.id
    )
    service.upsert_from_sync(
        ledgers=[{
            "name": "Sharma Traders",
            "group_name": "Sundry Debtors",
            "master_id": "local-guid-A",
        }],
        groups=[],
    )
    db_session.commit()

    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    assert row.tally_master_id == "local-guid-A"

    # Re-sync with a conflicting GUID — anomaly; local value held, WARN logged.
    with caplog.at_level(logging.WARNING, logger="app.services.ledger_service"):
        counts = service.upsert_from_sync(
            ledgers=[{
                "name": "Sharma Traders",
                "group_name": "Sundry Debtors",
                "master_id": "payload-guid-B",
            }],
            groups=[],
        )
        db_session.commit()

    assert counts == {"created": 0, "updated": 0, "skipped": 0}
    db_session.refresh(row)
    assert row.tally_master_id == "local-guid-A"  # PRESERVED, not overwritten

    mismatch_records = [
        r for r in caplog.records
        if r.name == "app.services.ledger_service"
        and r.levelname == "WARNING"
        and "reconciliation skipped" in r.getMessage()
    ]
    assert len(mismatch_records) == 1
    msg = mismatch_records[0].getMessage()
    assert "local-guid-A" in msg
    assert "payload-guid-B" in msg
    assert "Sharma Traders" in msg
    assert str(company.id) in msg

    audit_count = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.updated",
            AuditLog.entity_id == row.id,
        )
        .count()
    )
    assert audit_count == 0


def test_upsert_from_sync_stamps_tally_synced_at_on_every_processed_row(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(
        db_session, _audit(db_session, company, user), company.id
    )

    # Insert path.
    service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Debtors"}],
        groups=[],
    )
    db_session.commit()
    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Sharma Traders")
        .one()
    )
    first_stamp = row.tally_synced_at
    assert first_stamp is not None

    # Update path with semantic change (group_name moves).
    time.sleep(0.05)
    service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Creditors"}],
        groups=[],
    )
    db_session.commit()
    db_session.refresh(row)
    second_stamp = row.tally_synced_at
    assert second_stamp is not None
    assert second_stamp > first_stamp

    # Idempotent no-op (same data re-synced) — stamp still advances.
    time.sleep(0.05)
    service.upsert_from_sync(
        ledgers=[{"name": "Sharma Traders", "group_name": "Sundry Creditors"}],
        groups=[],
    )
    db_session.commit()
    db_session.refresh(row)
    third_stamp = row.tally_synced_at
    assert third_stamp is not None
    assert third_stamp > second_stamp


# ---------------------------------------------------------------------
# Wire-up: persist_sync_masters_payload helper
# ---------------------------------------------------------------------


def test_persist_sync_masters_payload_commits_and_attributes(
    db_session: Session,
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    # Helper opens its own session; the user/company must be committed
    # (which the factories already do) so the helper can read them.
    task_id = uuid4()
    counts = persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=task_id,
        ledgers=_sample_ledgers(),
        groups=[{"name": "Sundry Debtors", "parent": "Primary"}],
    )
    assert counts == {"created": 3, "updated": 0, "skipped": 0}

    rows = (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).all()
    )
    assert {r.name for r in rows} == {
        "Sharma Traders",
        "HDFC Bank A/c",
        "Sales",
    }

    audits = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.created",
        )
        .all()
    )
    assert len(audits) == 3
    assert all(a.user_id == user.id for a in audits)
    assert all(a.source == "connector" for a in audits)
    assert all(a.request_id == task_id for a in audits)


def test_persist_sync_masters_payload_isolates_tenants(
    db_session: Session,
) -> None:
    user_a = make_user(db_session)
    company_a = make_company(db_session, name="Acme")
    make_membership(db_session, user_a, company_a, role=CompanyRole.owner)

    user_b = make_user(db_session)
    company_b = make_company(db_session, name="Beta")
    make_membership(db_session, user_b, company_b, role=CompanyRole.owner)

    # Same logical names in two tenants — they must NOT collide.
    persist_sync_masters_payload(
        company_id=company_a.id,
        user_id=user_a.id,
        request_id=uuid4(),
        ledgers=[
            {"name": "Sharma Traders", "group_name": "Sundry Debtors"},
            {"name": "Sales", "group_name": "Sales Accounts"},
        ],
        groups=[],
    )
    persist_sync_masters_payload(
        company_id=company_b.id,
        user_id=user_b.id,
        request_id=uuid4(),
        ledgers=[
            {"name": "Sharma Traders", "group_name": "Sundry Debtors"},
            {"name": "Tea Expense", "group_name": "Indirect Expenses"},
        ],
        groups=[],
    )

    a_rows = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company_a.id)
        .all()
    )
    b_rows = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company_b.id)
        .all()
    )
    assert {r.name for r in a_rows} == {"Sharma Traders", "Sales"}
    assert {r.name for r in b_rows} == {"Sharma Traders", "Tea Expense"}

    # And the same-named ledgers really are separate rows.
    a_sharma = next(r for r in a_rows if r.name == "Sharma Traders")
    b_sharma = next(r for r in b_rows if r.name == "Sharma Traders")
    assert a_sharma.id != b_sharma.id
    assert a_sharma.company_id == company_a.id
    assert b_sharma.company_id == company_b.id


def test_persist_sync_masters_payload_idempotent(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        ledgers=_sample_ledgers(),
        groups=[],
    )
    counts = persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        ledgers=_sample_ledgers(),
        groups=[],
    )
    assert counts == {"created": 0, "updated": 0, "skipped": 0}
    assert (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id)
        .count()
        == 3
    )
