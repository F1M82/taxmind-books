"""Integration tests for sync_masters → ledger ingest (P0.46b / Phase 7B).

Covers the persistence path that the connector.py `_drive()` background
task invokes after `send_command` returns `status=success`. The full
WebSocket → command → reply loop is exercised in test_connector_sync.py;
here we test the persistence helper directly because the background
asyncio task is hard to await deterministically through TestClient.

Phase 7B: `upsert_from_sync` is GUID-first (identity `(company_id,
tally_master_id)`), and `persist_sync_masters_payload` enforces the
fail-closed company-mapping gate (a sync payload must carry the Tally
company GUID and it must match the local company's `tally_master_id`).
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from app.api.v1.connector import persist_sync_masters_payload
from app.core.audit import AuditContext, AuditEmitter
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.services.ledger_service import LedgerService
from app.services.tally.company_mapping import CompanyMappingError
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    make_company,
    make_membership,
    make_user,
)

# A stable Tally company GUID used to bind fixtures in these tests.
COMPANY_GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"


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
        {
            "name": "HDFC Bank A/c",
            "group_name": "Bank Accounts",
            "gstin": None,
            "master_id": "tally-hdfc-guid",
        },
        {
            "name": "Sales",
            "group_name": "Sales Accounts",
            "gstin": None,
            "master_id": "tally-sales-guid",
        },
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
    # GUID-first identity: every payload master_id is stored verbatim.
    assert {r.name: r.tally_master_id for r in rows} == {
        "HDFC Bank A/c": "tally-hdfc-guid",
        "Sales": "tally-sales-guid",
        "Sharma Traders": "tally-sharma-guid",
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
        {
            "name": "Sharma Traders",
            "group_name": "Sundry Creditors",
            "gstin": None,
            "master_id": "tally-sharma-guid",
        },
        {
            "name": "HDFC Bank A/c",
            "group_name": "Bank Accounts",
            "gstin": None,
            "master_id": "tally-hdfc-guid",
        },
        {
            "name": "Sales",
            "group_name": "Sales Accounts",
            "gstin": None,
            "master_id": "tally-sales-guid",
        },
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
    assert row.tally_master_id == "tally-sharma-guid"  # identity unchanged


def test_upsert_from_sync_reactivates_soft_deleted(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    service.upsert_from_sync(
        ledgers=[
            {
                "name": "Sharma Traders",
                "group_name": "Sundry Debtors",
                "master_id": "tally-sharma-guid",
            }
        ],
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
        ledgers=[
            {
                "name": "Sharma Traders",
                "group_name": "Sundry Debtors",
                "master_id": "tally-sharma-guid",
            }
        ],
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
            {"name": "Valid Ledger", "group_name": None, "master_id": "g-valid"},
            {"name": "", "group_name": None, "master_id": "g-a"},     # empty name
            {"name": "   ", "group_name": None, "master_id": "g-b"},  # whitespace
            {"group_name": "no-name", "master_id": "g-c"},            # missing name
            "not-a-dict",                                             # type-bad row
        ],
        groups=[],
    )
    db_session.commit()
    assert counts == {"created": 1, "updated": 0, "skipped": 4}


def test_upsert_from_sync_skips_missing_guid(db_session: Session) -> None:
    # Case E — a row without a Tally GUID is not persisted as a Tally master.
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    counts = service.upsert_from_sync(
        ledgers=[{"name": "No Guid Ledger", "group_name": None}],
        groups=[],
    )
    db_session.commit()
    assert counts == {"created": 0, "updated": 0, "skipped": 1}
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).count()
        == 0
    )


def test_upsert_from_sync_rejects_malformed_element(db_session: Session) -> None:
    # A GUID-less AND name-less element (the live Tally "malformed" master)
    # must be rejected — never persisted via name inference.
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    counts = service.upsert_from_sync(
        ledgers=[{"group_name": None}],
        groups=[],
    )
    db_session.commit()
    assert counts == {"created": 0, "updated": 0, "skipped": 1}
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).count()
        == 0
    )


def test_upsert_from_sync_same_name_different_guid_never_merged(
    db_session: Session,
) -> None:
    # Case C — same name, different GUID: never merge, never overwrite,
    # never re-insert (the (company_id, name) unique key forbids a dup name).
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    service = LedgerService(db_session, _audit(db_session, company, user), company.id)
    service.upsert_from_sync(
        ledgers=[
            {
                "name": "Cash",
                "group_name": "Cash-in-Hand",
                "master_id": "guid-existing",
            }
        ],
        groups=[],
    )
    db_session.commit()

    counts = service.upsert_from_sync(
        ledgers=[
            {"name": "Cash", "group_name": "Cash-in-Hand", "master_id": "guid-new"}
        ],
        groups=[],
    )
    db_session.commit()

    assert counts == {"created": 0, "updated": 0, "skipped": 1}
    row = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Cash")
        .one()
    )
    assert row.tally_master_id == "guid-existing"  # preserved, not overwritten


def test_upsert_from_sync_cross_company_guid_hard_stop(
    db_session: Session,
) -> None:
    # Case D — the same ledger GUID already belongs to another company →
    # hard stop, no write.
    user = make_user(db_session)
    co_a = make_company(db_session, name="Co A")
    co_b = make_company(db_session, name="Co B")
    make_membership(db_session, user, co_a, role=CompanyRole.owner)
    make_membership(db_session, user, co_b, role=CompanyRole.owner)

    service_a = LedgerService(db_session, _audit(db_session, co_a, user), co_a.id)
    service_a.upsert_from_sync(
        ledgers=[{"name": "Cash", "group_name": None, "master_id": "guid-x"}],
        groups=[],
    )
    db_session.commit()

    service_b = LedgerService(db_session, _audit(db_session, co_b, user), co_b.id)
    with pytest.raises(CompanyMappingError):
        service_b.upsert_from_sync(
            ledgers=[{"name": "Cash", "group_name": None, "master_id": "guid-x"}],
            groups=[],
        )
    db_session.rollback()
    # Co B wrote nothing.
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == co_b.id).count()
        == 0
    )


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
        ledgers=[
            {
                "name": "Sharma Traders",
                "group_name": "Sundry Debtors",
                "master_id": "tally-sharma-guid",
            }
        ],
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
        ledgers=[
            {
                "name": "Sharma Traders",
                "group_name": "Sundry Creditors",
                "master_id": "tally-sharma-guid",
            }
        ],
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
        ledgers=[
            {
                "name": "Sharma Traders",
                "group_name": "Sundry Creditors",
                "master_id": "tally-sharma-guid",
            }
        ],
        groups=[],
    )
    db_session.commit()
    db_session.refresh(row)
    third_stamp = row.tally_synced_at
    assert third_stamp is not None
    assert third_stamp > second_stamp


# ---------------------------------------------------------------------
# Wire-up: persist_sync_masters_payload helper (fail-closed gate)
# ---------------------------------------------------------------------


def _mapped_company(db: Session, name: str, guid: str = COMPANY_GUID) -> object:  # type: ignore[return-value]
    user = make_user(db)
    company = make_company(db, name=name, tally_master_id=guid)
    make_membership(db, user, company, role=CompanyRole.owner)
    return company, user


def test_persist_sync_masters_payload_commits_and_attributes(
    db_session: Session,
) -> None:
    company, user = _mapped_company(db_session, "Acme")

    task_id = uuid4()
    counts = persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=task_id,
        ledgers=_sample_ledgers(),
        groups=[{"name": "Sundry Debtors", "parent": "Primary"}],
        tally_company_guid=COMPANY_GUID,
        tally_company_name="Acme",
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
    company_a, user_a = _mapped_company(
        db_session, "Acme", "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"
    )
    company_b, user_b = _mapped_company(
        db_session, "Beta", "d41b0ee6-5fc5-4fdc-a10e-bd489d5423b9"
    )

    # Same logical names in two tenants — they must NOT collide.
    persist_sync_masters_payload(
        company_id=company_a.id,
        user_id=user_a.id,
        request_id=uuid4(),
        ledgers=[
            {"name": "Sharma Traders", "group_name": "Sundry Debtors", "master_id": "guid-a-sharma"},
            {"name": "Sales", "group_name": "Sales Accounts", "master_id": "guid-a-sales"},
        ],
        groups=[],
        tally_company_guid="c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9",
    )
    persist_sync_masters_payload(
        company_id=company_b.id,
        user_id=user_b.id,
        request_id=uuid4(),
        ledgers=[
            {"name": "Sharma Traders", "group_name": "Sundry Debtors", "master_id": "guid-b-sharma"},
            {"name": "Tea Expense", "group_name": "Indirect Expenses", "master_id": "guid-b-tea"},
        ],
        groups=[],
        tally_company_guid="d41b0ee6-5fc5-4fdc-a10e-bd489d5423b9",
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

    a_sharma = next(r for r in a_rows if r.name == "Sharma Traders")
    b_sharma = next(r for r in b_rows if r.name == "Sharma Traders")
    assert a_sharma.id != b_sharma.id
    assert a_sharma.company_id == company_a.id
    assert b_sharma.company_id == company_b.id


def test_persist_sync_masters_payload_idempotent(db_session: Session) -> None:
    company, user = _mapped_company(db_session, "Acme")

    persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        ledgers=_sample_ledgers(),
        groups=[],
        tally_company_guid=COMPANY_GUID,
    )
    counts = persist_sync_masters_payload(
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        ledgers=_sample_ledgers(),
        groups=[],
        tally_company_guid=COMPANY_GUID,
    )
    assert counts == {"created": 0, "updated": 0, "skipped": 0}
    assert (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id)
        .count()
        == 3
    )


def test_persist_sync_masters_payload_blocked_when_unmapped(
    db_session: Session,
) -> None:
    # No identity proof (company unmapped) → fail closed, zero writes.
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")  # no tally_master_id
    make_membership(db_session, user, company, role=CompanyRole.owner)

    with pytest.raises(CompanyMappingError):
        persist_sync_masters_payload(
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            ledgers=_sample_ledgers(),
            groups=[],
            tally_company_guid=COMPANY_GUID,
        )
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).count()
        == 0
    )


def test_persist_sync_masters_payload_blocked_on_guid_mismatch(
    db_session: Session,
) -> None:
    company, user = _mapped_company(db_session, "Acme")

    with pytest.raises(CompanyMappingError):
        persist_sync_masters_payload(
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            ledgers=_sample_ledgers(),
            groups=[],
            tally_company_guid="c30a0ee5-4fc5-4fdc-a10e-bd489d0000000",
        )
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).count()
        == 0
    )


def test_persist_sync_masters_payload_blocked_when_guid_missing(
    db_session: Session,
) -> None:
    company, user = _mapped_company(db_session, "Acme")

    with pytest.raises(CompanyMappingError):
        persist_sync_masters_payload(
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            ledgers=_sample_ledgers(),
            groups=[],
            tally_company_guid=None,
        )
    assert (
        db_session.query(Ledger).filter(Ledger.company_id == company.id).count()
        == 0
    )
