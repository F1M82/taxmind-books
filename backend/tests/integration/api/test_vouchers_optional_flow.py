"""Integration tests for the v1.2 Optional voucher endpoints (P0.46).

Covers POST /vouchers/{id}/approve-to-regular and
POST /vouchers/{id}/reject-optional. Uses a fake ConnectorRegistry so
the test doesn't need a live connector — only the contract between
the API layer and the registry matters here.
"""

from __future__ import annotations

from datetime import date
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
from app.services.tally import connector_registry as registry_mod
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user, company, *, idem: str | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    h = {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


# ---------------------------------------------------------------------
# Fake registry
# ---------------------------------------------------------------------


class _FakeRegistry:
    """Records every send_command and returns canned results.

    Default reply is `{status: success}`; tests can override per-command
    by setting `replies[command] = {...}` or raise via `errors[command]`.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.replies: dict[str, dict[str, Any]] = {}
        self.errors: dict[str, Exception] = {}

    async def send_command(
        self,
        *,
        company_id,  # type: ignore[no-untyped-def]
        command: str,
        args: dict[str, Any],
        timeout_seconds: int = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "company_id": company_id,
                "command": command,
                "args": args,
                "idempotency_key": idempotency_key,
            }
        )
        if command in self.errors:
            raise self.errors[command]
        return self.replies.get(command, {"status": "success", "result": {}})


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> _FakeRegistry:
    fake = _FakeRegistry()
    monkeypatch.setattr(registry_mod, "get_registry", lambda: fake)
    return fake


# ---------------------------------------------------------------------
# Fixture setup: an Optional voucher already posted to Tally
# ---------------------------------------------------------------------


def _setup_optional_voucher(  # type: ignore[no-untyped-def]
    db_session: Session,
    *,
    is_optional: bool = True,
    status_: VoucherStatus = VoucherStatus.optional,
    tally_voucher_guid: str | None = "GUID-1",
):
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(
        company_id=company.id, name="Bank", name_normalized="bank"
    )
    party = Ledger(
        company_id=company.id, name="Acme", name_normalized="acme"
    )
    db_session.add_all([bank, party])
    db_session.commit()
    v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Sales,
        date=date(2026, 5, 8),
        total_amount=Decimal("1000.00"),
        status=status_,
        source="photo",
        is_auto_posted=True,
        gst_applicable=False,
        is_optional_in_tally=is_optional,
        tally_voucher_guid=tally_voucher_guid,
    )
    db_session.add(v)
    db_session.flush()
    db_session.add_all(
        [
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=party.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=bank.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(v)
    return user, company, v


# ---------------------------------------------------------------------
# Approve-to-regular
# ---------------------------------------------------------------------


def test_approve_to_regular_calls_connector_and_updates_db(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company, idem=str(uuid4())),
        json={"notes": "Verified against bill"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_optional_in_tally"] is False
    assert body["status"] == "posted"
    assert body["approved_to_regular_at"] is not None
    assert body["approved_to_regular_by"] == str(user.id)

    # Connector got the approve command.
    assert len(fake_registry.calls) == 1
    call = fake_registry.calls[0]
    assert call["command"] == "approve_optional_voucher"
    assert call["args"] == {"tally_voucher_guid": "GUID-1"}

    db_session.expire_all()
    refreshed = db_session.query(Voucher).filter(Voucher.id == v.id).one()
    assert refreshed.is_optional_in_tally is False
    assert refreshed.status == VoucherStatus.posted
    assert refreshed.approved_to_regular_by == user.id


def test_approve_to_regular_writes_audit(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company, idem=str(uuid4())),
        json={},
    )
    assert r.status_code == 200
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.approved_to_regular",
        )
        .one()
    )
    assert audit.user_id == user.id
    assert audit.new_value["is_optional_in_tally"] is False


def test_approve_to_regular_already_regular_is_idempotent(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(
        db_session, is_optional=False, status_=VoucherStatus.posted
    )
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company, idem=str(uuid4())),
        json={},
    )
    assert r.status_code == 200
    assert r.json()["is_optional_in_tally"] is False
    # No connector round-trip when nothing to do.
    assert fake_registry.calls == []


def test_approve_to_regular_rejected_returns_409(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(
        db_session, status_=VoucherStatus.rejected_optional
    )
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company, idem=str(uuid4())),
        json={},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voucher_rejected"


def test_approve_to_regular_requires_idempotency_key(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company),
        json={},
    )
    assert r.status_code == 400


def test_approve_to_regular_503_when_connector_offline(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    fake_registry.errors["approve_optional_voucher"] = (
        registry_mod.ConnectorOffline("no active connector")
    )
    r = client.post(
        f"/api/v1/vouchers/{v.id}/approve-to-regular",
        headers=_h(user, company, idem=str(uuid4())),
        json={},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "connector_offline"


# ---------------------------------------------------------------------
# Reject-optional
# ---------------------------------------------------------------------


def test_reject_optional_calls_connector_and_marks_rejected(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    r = client.post(
        f"/api/v1/vouchers/{v.id}/reject-optional",
        headers=_h(user, company),
        json={"reason": "Personal expense"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected_optional"
    assert body["optional_rejection_reason"] == "Personal expense"
    assert body["optional_rejected_at"] is not None

    call = fake_registry.calls[0]
    assert call["command"] == "reject_optional_voucher"
    assert call["args"] == {"tally_voucher_guid": "GUID-1"}

    db_session.expire_all()
    refreshed = db_session.query(Voucher).filter(Voucher.id == v.id).one()
    assert refreshed.status == VoucherStatus.rejected_optional
    assert refreshed.optional_rejection_reason == "Personal expense"
    assert refreshed.optional_rejected_by == user.id


def test_reject_optional_on_regular_voucher_returns_409(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(
        db_session, is_optional=False, status_=VoucherStatus.posted
    )
    r = client.post(
        f"/api/v1/vouchers/{v.id}/reject-optional",
        headers=_h(user, company),
        json={"reason": "Test"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voucher_not_optional"
    assert fake_registry.calls == []


def test_reject_optional_writes_audit(
    client: TestClient, db_session: Session, fake_registry: _FakeRegistry
) -> None:
    user, company, v = _setup_optional_voucher(db_session)
    r = client.post(
        f"/api/v1/vouchers/{v.id}/reject-optional",
        headers=_h(user, company),
        json={"reason": "Duplicate"},
    )
    assert r.status_code == 200
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.rejected_optional",
        )
        .one()
    )
    assert audit.new_value["status"] == "rejected_optional"
    assert audit.new_value["optional_rejection_reason"] == "Duplicate"
