"""Integration tests for GET /vouchers/ + GET /vouchers/{id} (P0.19)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

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


def _seed_voucher(
    db,  # type: ignore[no-untyped-def]
    *,
    company,
    bank,
    party,
    vtype: VoucherType = VoucherType.Receipt,
    amount: str = "1000.00",
    on: date = date(2026, 5, 8),
    status_: VoucherStatus = VoucherStatus.posted,
):  # type: ignore[no-untyped-def]
    v = Voucher(
        company_id=company.id,
        voucher_type=vtype,
        date=on,
        total_amount=Decimal(amount),
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
                amount=Decimal(amount),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=party.id,
                amount=Decimal(amount),
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


# ---------------- GET /{id} ----------------


def test_get_voucher_returns_with_entries(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    v = _seed_voucher(db_session, company=company, bank=bank, party=party)
    r = client.get(f"/api/v1/vouchers/{v.id}", headers=_h(user, company))
    assert r.status_code == 200
    body = r.json()
    assert body["voucher_type"] == "Receipt"
    assert len(body["entries"]) == 2
    assert body["entries"][0]["line_number"] == 1


def test_get_voucher_404_unknown(
    client: TestClient, db_session: Session
) -> None:
    user, company, _, _ = _setup(db_session)
    r = client.get(f"/api/v1/vouchers/{uuid4()}", headers=_h(user, company))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "voucher_not_found"


# ---------------- GET / ----------------


def test_list_vouchers_returns_only_active_company(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    _seed_voucher(db_session, company=company, bank=bank, party=party)
    _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        amount="2000.00",
    )
    r = client.get("/api/v1/vouchers/", headers=_h(user, company))
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 2


def test_list_filter_by_voucher_type(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    _seed_voucher(db_session, company=company, bank=bank, party=party,
                  vtype=VoucherType.Receipt)
    _seed_voucher(db_session, company=company, bank=bank, party=party,
                  vtype=VoucherType.Payment)
    r = client.get(
        "/api/v1/vouchers/?voucher_type=Receipt", headers=_h(user, company)
    )
    assert r.status_code == 200
    types = {item["voucher_type"] for item in r.json()["items"]}
    assert types == {"Receipt"}


def test_list_filter_by_date_range(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    _seed_voucher(db_session, company=company, bank=bank, party=party,
                  on=date(2026, 4, 1))
    _seed_voucher(db_session, company=company, bank=bank, party=party,
                  on=date(2026, 5, 1))
    _seed_voucher(db_session, company=company, bank=bank, party=party,
                  on=date(2026, 6, 1))
    r = client.get(
        "/api/v1/vouchers/?from=2026-04-15&to=2026-05-15",
        headers=_h(user, company),
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


def test_list_filter_by_status(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    _seed_voucher(db_session, company=company, bank=bank, party=party)
    _seed_voucher(
        db_session,
        company=company,
        bank=bank,
        party=party,
        status_=VoucherStatus.cancelled,
    )
    r = client.get(
        "/api/v1/vouchers/?status=cancelled", headers=_h(user, company)
    )
    assert r.status_code == 200
    statuses = {item["status"] for item in r.json()["items"]}
    assert statuses == {"cancelled"}


def test_list_filter_by_ledger_id(
    client: TestClient, db_session: Session
) -> None:
    user, company, bank, party = _setup(db_session)
    other = Ledger(
        company_id=company.id, name="Other", name_normalized="other"
    )
    db_session.add(other)
    db_session.commit()
    _seed_voucher(db_session, company=company, bank=bank, party=party)
    _seed_voucher(db_session, company=company, bank=bank, party=other)
    r = client.get(
        f"/api/v1/vouchers/?ledger_id={party.id}", headers=_h(user, company)
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1
