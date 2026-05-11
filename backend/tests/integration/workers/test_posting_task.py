"""Integration tests for the voucher_dispatcher (P0.26).

Exercises the async dispatcher with a mocked ConnectorRegistry —
the registry's `send_command` is the boundary between the
voucher_dispatcher and the actual WebSocket. A test fake stands in
for the registry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.tally.connector_registry import (
    CommandTimeout,
    ConnectorOffline,
    ConnectorRegistry,
)
from app.services.tally.voucher_dispatcher import dispatch_voucher_to_tally
from sqlalchemy.orm import Session
from tests._db_fixtures import make_company, make_membership, make_user


class _FakeRegistry(ConnectorRegistry):
    """Records args, returns a canned reply."""

    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        super().__init__()
        self.reply = reply
        self.received_args: dict[str, Any] | None = None

    async def send_command(  # type: ignore[override]
        self,
        *,
        company_id,  # type: ignore[no-untyped-def]
        command: str,
        args: dict[str, Any],
        timeout_seconds: int = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.received_args = {
            "company_id": company_id,
            "command": command,
            "args": args,
            "idempotency_key": idempotency_key,
        }
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _seed_voucher(
    db,  # type: ignore[no-untyped-def]
    *,
    company,
    bank,
    party,
    voucher_type: VoucherType = VoucherType.Receipt,
):  # type: ignore[no-untyped-def]
    v = Voucher(
        company_id=company.id,
        voucher_type=voucher_type,
        voucher_number="R-1",
        date=date(2026, 5, 8),
        narration="Payment received",
        total_amount=Decimal("1000.00"),
        status=VoucherStatus.posted,
        source="manual",
        is_auto_posted=False,
        gst_applicable=False,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=bank.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=party.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db.commit()
    db.refresh(v)
    return v


def _setup(db_session: Session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(company_id=company.id, name="Bank", name_normalized="bank")
    party = Ledger(
        company_id=company.id, name="Sharma", name_normalized="sharma"
    )
    db_session.add_all([bank, party])
    db_session.commit()
    return user, company, bank, party


# ---------------- success ----------------


@pytest.mark.asyncio
async def test_dispatch_success_stamps_tally_posted_at(
    db_session: Session,
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)

    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "success",
            "result": {
                "tally_voucher_guid": "tally-guid-001",
                "tally_voucher_number": "RCT-001",
            },
            "duration_ms": 250,
        }
    )
    result = await dispatch_voucher_to_tally(
        db=db_session,
        voucher_id=v.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
        timeout_seconds=5,
    )
    db_session.commit()
    db_session.refresh(v)

    assert result["status"] == "success"
    assert v.tally_posted_at is not None
    assert v.tally_voucher_guid == "tally-guid-001"
    assert v.tally_last_error is None
    # send_command was called with the right shape.
    args = reg.received_args["args"]
    assert args["voucher_type"] == "Receipt"
    assert args["date"] == "2026-05-08"
    assert len(args["entries"]) == 2
    # Idempotency key = voucher id, used by the connector's local cache.
    assert reg.received_args["idempotency_key"] == str(v.id)


@pytest.mark.asyncio
async def test_dispatch_success_writes_audit(db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "success",
            "result": {"tally_voucher_guid": "g-1"},
            "duration_ms": 100,
        }
    )
    await dispatch_voucher_to_tally(
        db=db_session,
        voucher_id=v.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
    )
    db_session.commit()

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.posted_to_tally",
        )
        .one()
    )
    assert audit.source == "worker"
    assert audit.company_id == company.id
    assert audit.user_id == user.id  # the actor who created the voucher
    assert audit.new_value["tally_voucher_guid"] == "g-1"


# ---------------- offline / timeout (retryable) ----------------


@pytest.mark.asyncio
async def test_dispatch_connector_offline_increments_attempts_and_re_raises(
    db_session: Session,
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(reply=ConnectorOffline("no connector"))

    with pytest.raises(ConnectorOffline):
        await dispatch_voucher_to_tally(
            db=db_session,
            voucher_id=v.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
    db_session.commit()
    db_session.refresh(v)
    assert v.tally_post_attempts == 1
    assert v.tally_last_error is not None
    assert v.tally_posted_at is None
    # Audit failure row written.
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_failed",
        )
        .one()
    )
    assert audit.new_value["error_class"] == "ConnectorOffline"


@pytest.mark.asyncio
async def test_dispatch_command_timeout_re_raises(
    db_session: Session,
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(reply=CommandTimeout("connector slow"))
    with pytest.raises(CommandTimeout):
        await dispatch_voucher_to_tally(
            db=db_session,
            voucher_id=v.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
    db_session.commit()
    db_session.refresh(v)
    assert v.tally_post_attempts == 1


# ---------------- connector returns error status (non-retryable path) -


@pytest.mark.asyncio
async def test_dispatch_error_status_audits_failure(
    db_session: Session,
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "error",
            "error": {
                "code": "tally_validation_failed",
                "message": "Ledger not found in Tally",
            },
            "retryable": False,
        }
    )
    result = await dispatch_voucher_to_tally(
        db=db_session,
        voucher_id=v.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
    )
    db_session.commit()
    db_session.refresh(v)

    assert result["status"] == "error"
    assert v.tally_post_attempts == 1
    assert v.tally_posted_at is None
    assert v.tally_last_error is not None
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_failed",
        )
        .one()
    )
    assert audit.new_value["error"]["code"] == "tally_validation_failed"


# ---------------- voucher not found in company ----------------


@pytest.mark.asyncio
async def test_dispatch_raises_when_voucher_in_other_company(
    db_session: Session,
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    other_company = make_company(db_session, name="Other")
    reg = _FakeRegistry(reply={"status": "success", "result": {}})

    with pytest.raises(ValueError):
        await dispatch_voucher_to_tally(
            db=db_session,
            voucher_id=v.id,
            company_id=other_company.id,  # wrong company
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
