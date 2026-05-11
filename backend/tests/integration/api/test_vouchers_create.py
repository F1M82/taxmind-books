"""Integration tests for POST /api/v1/vouchers/ (P0.18)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import LedgerEntry, Voucher, VoucherStatus
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


def _setup(db_session: Session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(
        company_id=company.id, name="Bank", name_normalized="bank"
    )
    party = Ledger(
        company_id=company.id, name="Sharma", name_normalized="sharma"
    )
    db_session.add_all([bank, party])
    db_session.commit()
    return user, company, bank, party


def _payload(bank: Ledger, party: Ledger, **overrides):  # type: ignore[no-untyped-def]
    base = {
        "voucher_type": "Receipt",
        "date": "2026-05-08",
        "narration": "Payment from Sharma",
        "reference": "UTR1234",
        "total_amount": "50000.00",
        "entries": [
            {
                "ledger_id": str(bank.id),
                "amount": "50000.00",
                "entry_type": "Dr",
            },
            {
                "ledger_id": str(party.id),
                "amount": "50000.00",
                "entry_type": "Cr",
            },
        ],
        "gst_applicable": False,
    }
    base.update(overrides)
    return base


# ---------------- Happy path ----------------


def test_create_voucher_201(client: TestClient, db_session: Session) -> None:
    user, company, bank, party = _setup(db_session)
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=_payload(bank, party),
    )
    assert r.status_code == 201, r.json()
    body = r.json()
    UUID(body["id"])
    assert body["voucher_type"] == "Receipt"
    assert body["status"] == "posted"
    assert body["source"] == "manual"
    assert Decimal(body["total_amount"]) == Decimal("50000.00")
    assert len(body["entries"]) == 2
    assert body["entries"][0]["line_number"] == 1
    assert body["entries"][1]["line_number"] == 2

    db_session.expire_all()
    voucher = (
        db_session.query(Voucher).filter(Voucher.id == UUID(body["id"])).one()
    )
    assert voucher.status == VoucherStatus.posted
    assert voucher.created_by == user.id
    entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.voucher_id == voucher.id
    ).all()
    assert len(entries) == 2


def test_create_voucher_writes_audit(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=_payload(bank, party),
    )
    assert r.status_code == 201
    vid = UUID(r.json()["id"])
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == vid,
            AuditLog.action == "voucher.created",
        )
        .one()
    )
    assert audit.user_id == user.id
    assert audit.company_id == company.id
    assert audit.new_value["status"] == "posted"
    assert len(audit.new_value["entries"]) == 2


# ---------------- Idempotency ----------------


def test_create_voucher_missing_idempotency_400(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company),  # no Idempotency-Key
        json=_payload(bank, party),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_required"


def test_create_voucher_replay_returns_stored_response(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    key = str(uuid4())
    payload = _payload(bank, party)
    r1 = client.post(
        "/api/v1/vouchers/", headers=_h(user, company, idem=key), json=payload
    )
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    r2 = client.post(
        "/api/v1/vouchers/", headers=_h(user, company, idem=key), json=payload
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id
    assert r2.headers.get("Idempotent-Replay") == "true"

    # Only one voucher exists.
    assert db_session.query(Voucher).count() == 1


def test_create_voucher_replay_with_different_body_409(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    key = str(uuid4())
    r1 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=key),
        json=_payload(bank, party),
    )
    assert r1.status_code == 201
    r2 = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=key),
        json=_payload(bank, party, total_amount="60000.00"),  # mutated
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_replay"


# ---------------- Validation ----------------


def test_create_voucher_unbalanced_dr_cr(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(bank, party)
    payload["entries"][1]["amount"] = "40000.00"  # Cr now mismatches Dr
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "voucher_entries_unbalanced"


def test_create_voucher_total_mismatch_with_dr_total(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(bank, party, total_amount="40000.00")
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "voucher_entries_unbalanced"


def test_create_voucher_single_entry_rejected(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, _ = _setup(db_session)
    payload = {
        "voucher_type": "Receipt",
        "date": "2026-05-08",
        "total_amount": "50000.00",
        "entries": [
            {
                "ledger_id": str(bank.id),
                "amount": "50000.00",
                "entry_type": "Dr",
            }
        ],
    }
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422


def test_create_voucher_ledger_in_other_company_422(
    client: TestClient, db_session: Session
) -> None:
    """Use a ledger ID from another company → ownership check fails."""
    user, company, bank, _ = _setup(db_session)
    other = make_company(db_session, name="Other")
    other_ledger = Ledger(
        company_id=other.id,
        name="OtherL",
        name_normalized="otherl",
    )
    db_session.add(other_ledger)
    db_session.commit()

    payload = _payload(bank, other_ledger)
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ledger_not_found"


def test_create_voucher_gst_without_place_of_supply_422(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(
        bank,
        party,
        gst_applicable=True,
        cgst="100.00",
        sgst="100.00",
    )
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_create_voucher_with_gst_succeeds(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(
        bank,
        party,
        gst_applicable=True,
        place_of_supply="27",
        cgst="100.00",
        sgst="100.00",
        igst="0.00",
        cess="0.00",
    )
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 201, r.json()


def test_create_voucher_money_rejects_float(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(bank, party)
    payload["total_amount"] = 50000.0  # float
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422


def test_create_voucher_invalid_voucher_type_422(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    payload = _payload(bank, party, voucher_type="InvalidType")
    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=payload,
    )
    assert r.status_code == 422


def test_create_voucher_requires_x_company_id(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.post(
        "/api/v1/vouchers/",
        headers={
            "Authorization": f"Bearer {issue_token(user)}",
            "Idempotency-Key": str(uuid4()),
        },
        json={"voucher_type": "Receipt"},
    )
    assert r.status_code == 422
