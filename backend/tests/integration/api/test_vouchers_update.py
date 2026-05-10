"""Integration tests for PATCH /api/v1/vouchers/{id} (P0.19)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _setup_with_voucher(db_session: Session, *, status_=VoucherStatus.posted):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(company_id=company.id, name="Bank", name_normalized="bank")
    party = Ledger(
        company_id=company.id, name="Sharma", name_normalized="sharma"
    )
    db_session.add_all([bank, party])
    db_session.commit()
    v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Receipt,
        date=date(2026, 5, 8),
        narration="Original narration",
        reference="REF-001",
        total_amount=Decimal("1000.00"),
        status=status_,
        source="manual",
        is_auto_posted=False,
        gst_applicable=False,
    )
    db_session.add(v)
    db_session.flush()
    db_session.add_all(
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
    db_session.commit()
    db_session.refresh(v)
    return user, company, v


def test_patch_narration_succeeds(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"narration": "Updated narration"},
    )
    assert r.status_code == 200
    assert r.json()["narration"] == "Updated narration"


def test_patch_reference_succeeds(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"reference": "NEW-REF-002"},
    )
    assert r.status_code == 200
    assert r.json()["reference"] == "NEW-REF-002"


def test_patch_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"narration": "After"},
    )
    assert r.status_code == 200
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.updated",
        )
        .one()
    )
    assert "narration" in audit.changes
    assert audit.changes["narration"] == ["Original narration", "After"]


def test_patch_immutable_field_409(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"total_amount": "9999.00"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voucher_immutable_field"
    assert "total_amount" in r.json()["error"]["details"]["fields"]


def test_patch_immutable_voucher_type_409(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"voucher_type": "Payment"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voucher_immutable_field"


def test_patch_cancelled_voucher_409(
    client: TestClient, db_session: Session
) -> None:
    user, company, v = _setup_with_voucher(
        db_session, status_=VoucherStatus.cancelled
    )
    r = client.patch(
        f"/api/v1/vouchers/{v.id}",
        headers=_h(user, company),
        json={"narration": "Try update"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voucher_already_cancelled"


def test_patch_unknown_voucher_404(
    client: TestClient, db_session: Session
) -> None:
    from uuid import uuid4

    user, company, _ = _setup_with_voucher(db_session)
    r = client.patch(
        f"/api/v1/vouchers/{uuid4()}",
        headers=_h(user, company),
        json={"narration": "x"},
    )
    assert r.status_code == 404
