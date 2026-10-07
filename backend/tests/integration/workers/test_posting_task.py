"""Integration tests for the voucher_dispatcher (P0.26).

Exercises the async dispatcher with a mocked ConnectorRegistry —
the registry's `send_command` is the boundary between the
voucher_dispatcher and the actual WebSocket. A test fake stands in
for the registry.
"""

from __future__ import annotations

from datetime import UTC, datetime, date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.connector import Connector, ConnectorCompanyBinding
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
    ConnectorConnection,
    ConnectorOffline,
    ConnectorRegistry,
    TallyRejectedEnvelope,
    TallyRetryableEnvelope,
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


def _attach_connection(
    reg: ConnectorRegistry, company_id, connector_id  # type: ignore[no-untyped-def]
) -> None:
    """Seed the registry as if the connector were live.

    `send_command` is overridden by _FakeRegistry, so the connection's
    WebSocket is never touched — None is safe here.
    """
    reg._by_company[company_id] = ConnectorConnection(
        company_id=company_id,
        connector_id=connector_id,
        ws=None,  # type: ignore[arg-type]
    )


def _seed_voucher(
    db,  # type: ignore[no-untyped-def]
    *,
    company,
    bank,
    party,
    voucher_type: VoucherType = VoucherType.Receipt,
    status_: VoucherStatus = VoucherStatus.posted,
):  # type: ignore[no-untyped-def]
    v = Voucher(
        company_id=company.id,
        voucher_type=voucher_type,
        voucher_number="R-1",
        date=date(2026, 5, 8),
        narration="Payment received",
        total_amount=Decimal("1000.00"),
        status=status_,
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
    # BUG-004 Layer C: the backend voucher id is passed so the connector
    # can stamp it as the Tally REMOTEID on Create.
    assert args["voucher_id"] == str(v.id)
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
    # P0.46d: retryable failures emit `voucher.tally_post_queued`
    # (renamed from voucher.tally_post_failed). Non-retryable connector
    # errors still emit voucher.tally_post_failed — see the error-status
    # test below.
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_queued",
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
async def test_dispatch_envelope_retryable_false_raises_rejected_envelope(
    db_session: Session,
) -> None:
    """BUG-Books-004 Layer A/B: a non-retryable error envelope (Tally
    rejected for operator-fixable reason — e.g. ledger missing) now
    raises TallyRejectedEnvelope instead of returning the result dict.
    The voucher.tally_post_failed audit row is emitted before the raise
    so _drive / the worker can commit it before the exception unwinds.
    """
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "error",
            "error": {
                "code": "TallyImportRejected",
                "message": "Ledger 'Sales' does not exist!",
            },
            "retryable": False,
        }
    )
    with pytest.raises(TallyRejectedEnvelope) as exc_info:
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

    assert exc_info.value.error_code == "TallyImportRejected"
    assert v.tally_post_attempts == 1
    assert v.tally_posted_at is None
    assert v.tally_last_error == "Ledger 'Sales' does not exist!"
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_failed",
        )
        .one()
    )
    assert audit.new_value["error"]["code"] == "TallyImportRejected"
    assert audit.new_value["error_class"] == "TallyImportRejected"


@pytest.mark.asyncio
async def test_dispatch_envelope_retryable_true_raises_retryable_envelope(
    db_session: Session,
) -> None:
    """BUG-Books-004 Layer B: a retryable error envelope (Tally
    transport blip, ambiguous response) now emits
    voucher.tally_post_queued (same action as ConnectorOffline /
    CommandTimeout — symmetric) and raises TallyRetryableEnvelope
    instead of returning.
    """
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "error",
            "error": {
                "code": "TallyUnreachable",
                "message": "All connection attempts failed",
            },
            "retryable": True,
        }
    )
    with pytest.raises(TallyRetryableEnvelope) as exc_info:
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

    assert exc_info.value.error_code == "TallyUnreachable"
    assert v.tally_post_attempts == 1
    assert v.tally_posted_at is None
    assert v.tally_last_error == "All connection attempts failed"
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_queued",
        )
        .one()
    )
    assert audit.new_value["error_class"] == "TallyUnreachable"


# ---------------- v1.3 wrong-company guard (target identifier) ---------


_WrongCompanyOpen_REPLY = {
    "command": "post_voucher",
    "status": "error",
    "error": {
        "code": "wrong_company_open",
        "message": "requested company is not currently open in Tally",
    },
    "retryable": True,
}


def _bind_connector(
    db: Session, company, *, identifier: str
):  # type: ignore[no-untyped-def]
    """Persist a Connector + ConnectorCompanyBinding pair, as the
    tally-mapping endpoint would for a discovery-based mapping."""
    connector_id = uuid4()
    db.add(Connector(id=connector_id))
    db.flush()
    db.add(
        ConnectorCompanyBinding(
            connector_id=connector_id,
            company_id=company.id,
            data_folder_path="C:/Tally.9000",
            tally_company_identifier=identifier,
            tally_company_display_name="Vighnaharta Agro Chemicals",
            configured_at=datetime.now(UTC),
        )
    )
    db.commit()
    return connector_id


@pytest.mark.asyncio
async def test_dispatch_sends_target_tally_company_identifier(
    db_session: Session,
) -> None:
    """v1.3 queue-on-mismatch: when the company has a connector binding,
    the post_voucher args carry the binding's tally_company_identifier so
    the connector refuses to write into a different open Tally company."""
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.pending_tally_post,
    )
    connector_id = _bind_connector(
        db_session, company, identifier="10000"
    )
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "success",
            "result": {"tally_voucher_guid": "g-1"},
            "duration_ms": 10,
        }
    )
    _attach_connection(reg, company.id, connector_id)

    await dispatch_voucher_to_tally(
        db=db_session,
        voucher_id=v.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
    )
    db_session.commit()

    assert reg.received_args is not None
    assert (
        reg.received_args["args"]["target_tally_company_identifier"]
        == "10000"
    )


@pytest.mark.asyncio
async def test_dispatch_omits_target_identifier_without_binding(
    db_session: Session,
) -> None:
    """Companies mapped only via Company.tally_master_id (GUID-only legacy
    mappings, zero ConnectorCompanyBinding rows) must keep dispatching —
    the guard field is omitted, the connector behaves exactly as before."""
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.pending_tally_post,
    )
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "success",
            "result": {"tally_voucher_guid": "g-1"},
            "duration_ms": 10,
        }
    )
    _attach_connection(reg, company.id, uuid4())  # live connector, no binding

    await dispatch_voucher_to_tally(
        db=db_session,
        voucher_id=v.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
    )
    db_session.commit()

    assert reg.received_args is not None
    assert "target_tally_company_identifier" not in reg.received_args["args"]


@pytest.mark.asyncio
async def test_dispatch_wrong_company_open_keeps_pending_and_raises_retryable(
    db_session: Session,
) -> None:
    """The connector refuses to post into the wrong Tally company
    (wrong_company_open, retryable=True). The dispatcher must:
    raise TallyRetryableEnvelope, emit voucher.tally_post_queued, and
    leave the voucher in pending_tally_post (queue-on-mismatch) — the
    state the re-enqueue sweep later drains."""
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.pending_tally_post,
    )
    connector_id = _bind_connector(
        db_session, company, identifier="10000"
    )
    reg = _FakeRegistry(reply=dict(_WrongCompanyOpen_REPLY))
    _attach_connection(reg, company.id, connector_id)

    with pytest.raises(TallyRetryableEnvelope) as exc_info:
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

    # The guard field reached the connector so it COULD make this check.
    assert reg.received_args is not None
    assert (
        reg.received_args["args"]["target_tally_company_identifier"]
        == "10000"
    )
    # Queue-on-mismatch outcome.
    assert exc_info.value.error_code == "wrong_company_open"
    assert v.status == VoucherStatus.pending_tally_post
    assert v.tally_posted_at is None
    assert v.tally_post_attempts == 1
    assert v.tally_last_error == (
        "requested company is not currently open in Tally"
    )
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_queued",
        )
        .one()
    )
    assert audit.new_value["error"]["code"] == "wrong_company_open"


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


# ---------------- P0.46d: pending_tally_post lifecycle ----------------


@pytest.mark.asyncio
async def test_dispatch_success_transitions_pending_to_posted(
    db_session: Session,
) -> None:
    """Voucher created via the API lands in pending_tally_post;
    the dispatcher is the only thing that flips it to posted, and
    only on Tally success."""
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.pending_tally_post,
    )
    reg = _FakeRegistry(
        reply={
            "command": "post_voucher",
            "status": "success",
            "result": {"tally_voucher_guid": "g-pending"},
            "duration_ms": 80,
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
    db_session.refresh(v)
    assert v.status == VoucherStatus.posted
    assert v.tally_posted_at is not None


@pytest.mark.asyncio
async def test_dispatch_offline_keeps_pending_and_emits_queued_audit(
    db_session: Session,
) -> None:
    """ConnectorOffline must not advance the voucher out of
    pending_tally_post — the queue is the whole point of the state."""
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.pending_tally_post,
    )
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
    assert v.status == VoucherStatus.pending_tally_post
    assert v.tally_posted_at is None
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_queued",
        )
        .one()
    )
    assert audit.new_value["error_class"] == "ConnectorOffline"
