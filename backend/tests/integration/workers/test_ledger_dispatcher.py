"""Integration tests for ledger_dispatcher (v1.3 item 7).

Exercises the async dispatcher with a mocked ConnectorRegistry, mirroring
test_posting_task.py's `_FakeRegistry` pattern -- the registry's
`send_command` is the boundary between the dispatcher and the actual
WebSocket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.connector import Connector, ConnectorCompanyBinding
from app.models.ledger import BalanceType, Ledger
from app.services.tally.connector_registry import (
    ConnectorConnection,
    ConnectorOffline,
    ConnectorRegistry,
    TallyRejectedEnvelope,
    TallyRetryableEnvelope,
)
from app.services.tally.ledger_dispatcher import dispatch_ledger_to_tally
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
    reg._by_company[company_id] = ConnectorConnection(
        company_id=company_id,
        connector_id=connector_id,
        ws=None,  # type: ignore[arg-type]
    )


def _bind_connector(
    db: Session, company, *, identifier: str
):  # type: ignore[no-untyped-def]
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


def _setup(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.owner)
    ledger = Ledger(
        company_id=company.id,
        name="New Customer",
        name_normalized="new customer",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
        created_via_mobile=True,
    )
    db.add(ledger)
    db.commit()
    return user, company, ledger


@pytest.mark.asyncio
async def test_dispatch_success_confirms_ledger_and_writes_audit(
    db_session: Session,
) -> None:
    user, company, ledger = _setup(db_session)
    reg = _FakeRegistry(
        reply={
            "command": "create_ledger",
            "status": "success",
            "tally_master_id": str(ledger.id),
            "duration_ms": 12,
        }
    )
    _attach_connection(reg, company.id, uuid4())

    await dispatch_ledger_to_tally(
        db=db_session,
        ledger_id=ledger.id,
        company_id=company.id,
        user_id=user.id,
        request_id=uuid4(),
        registry=reg,
    )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.query(Ledger).filter(Ledger.id == ledger.id).one()
    assert refreshed.tally_master_id == str(ledger.id)
    assert refreshed.confirmed_in_tally_at is not None
    assert refreshed.tally_synced_at is not None

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.confirmed_in_tally",
            AuditLog.entity_id == ledger.id,
        )
        .one()
    )
    assert audit.new_value["tally_master_id"] == str(ledger.id)


@pytest.mark.asyncio
async def test_dispatch_sends_target_tally_company_identifier(
    db_session: Session,
) -> None:
    """Same v1.3 guard field as voucher posting -- a ledger must land in
    the right company's Tally data just as much as a voucher."""
    user, company, ledger = _setup(db_session)
    connector_id = _bind_connector(db_session, company, identifier="10000")
    reg = _FakeRegistry(
        reply={
            "command": "create_ledger",
            "status": "success",
            "tally_master_id": str(ledger.id),
            "duration_ms": 12,
        }
    )
    _attach_connection(reg, company.id, connector_id)

    await dispatch_ledger_to_tally(
        db=db_session,
        ledger_id=ledger.id,
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
async def test_dispatch_connector_offline_leaves_ledger_unconfirmed(
    db_session: Session,
) -> None:
    user, company, ledger = _setup(db_session)
    reg = _FakeRegistry(reply=ConnectorOffline("no connector"))
    _attach_connection(reg, company.id, uuid4())

    with pytest.raises(ConnectorOffline):
        await dispatch_ledger_to_tally(
            db=db_session,
            ledger_id=ledger.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.query(Ledger).filter(Ledger.id == ledger.id).one()
    assert refreshed.confirmed_in_tally_at is None
    assert refreshed.tally_master_id is None

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.sync_failed",
            AuditLog.entity_id == ledger.id,
        )
        .one()
    )
    assert audit.new_value["error_class"] == "ConnectorOffline"


@pytest.mark.asyncio
async def test_dispatch_rejected_envelope_raises_tally_rejected(
    db_session: Session,
) -> None:
    """A real Tally rejection (e.g. duplicate name, bad group) is NOT
    retryable -- the operator has to fix it, matching post_voucher's
    rejection-class handling."""
    user, company, ledger = _setup(db_session)
    reg = _FakeRegistry(
        reply={
            "command": "create_ledger",
            "status": "error",
            "error": {
                "code": "tally_import_rejected",
                "message": "ledger already exists",
            },
            "retryable": False,
            "duration_ms": 8,
        }
    )
    _attach_connection(reg, company.id, uuid4())

    with pytest.raises(TallyRejectedEnvelope):
        await dispatch_ledger_to_tally(
            db=db_session,
            ledger_id=ledger.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.query(Ledger).filter(Ledger.id == ledger.id).one()
    assert refreshed.confirmed_in_tally_at is None


@pytest.mark.asyncio
async def test_dispatch_success_without_master_id_raises_retryable(
    db_session: Session,
) -> None:
    """A "success" result with no tally_master_id is a contract
    violation (live-verified 2026-10-07: Tally doesn't honor REMOTEID on
    a LEDGER Create, so the connector must always confirm the real GUID
    by read-back -- see TallyClient.create_ledger). Must never fall back
    to a made-up id; must raise retryable instead so a retry either
    gets a clean confirmation or a clean "already exists" rejection."""
    user, company, ledger = _setup(db_session)
    reg = _FakeRegistry(
        reply={
            "command": "create_ledger",
            "status": "success",
            # tally_master_id deliberately omitted.
            "duration_ms": 12,
        }
    )
    _attach_connection(reg, company.id, uuid4())

    with pytest.raises(TallyRetryableEnvelope):
        await dispatch_ledger_to_tally(
            db=db_session,
            ledger_id=ledger.id,
            company_id=company.id,
            user_id=user.id,
            request_id=uuid4(),
            registry=reg,
        )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.query(Ledger).filter(Ledger.id == ledger.id).one()
    assert refreshed.confirmed_in_tally_at is None
    assert refreshed.tally_master_id is None

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "ledger.sync_failed",
            AuditLog.entity_id == ledger.id,
        )
        .one()
    )
    assert audit.new_value["error_class"] == "TallyAmbiguousResponse"
