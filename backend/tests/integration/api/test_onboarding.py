"""Integration tests for GET /api/v1/onboarding/checklist (P0.42)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.company import CompanyRole
from app.models.connector_enrollment import ConnectorEnrollmentCode
from app.models.ledger import BalanceType, Ledger
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


def _seed(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db, name="Acme Traders")
    make_membership(db, user, company, role=CompanyRole.owner)
    return user, company


def _post_voucher(  # type: ignore[no-untyped-def]
    db: Session, company_id, dr_ledger: Ledger, cr_ledger: Ledger
) -> Voucher:
    v = Voucher(
        company_id=company_id,
        voucher_type=VoucherType.Receipt,
        date=date.today(),
        total_amount=Decimal("100.00"),
        status=VoucherStatus.posted,
        source="manual",
        is_auto_posted=False,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company_id,
                voucher_id=v.id,
                ledger_id=dr_ledger.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company_id,
                voucher_id=v.id,
                ledger_id=cr_ledger.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db.commit()
    return v


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_fresh_company_has_only_company_created_complete(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)

    r = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["company_id"] == str(company.id)
    assert body["total_count"] == 5
    assert body["completed_count"] == 1

    by_key = {i["key"]: i for i in body["items"]}
    assert set(by_key) == {
        "company_created",
        "connector_installed",
        "ledgers_synced",
        "first_voucher_posted",
        "first_invoice_extracted",
    }

    # Only company_created carries completed_at; the rest must omit it
    # (response_model_exclude_none=True).
    assert by_key["company_created"]["completed"] is True
    assert "completed_at" in by_key["company_created"]
    for key in (
        "connector_installed",
        "ledgers_synced",
        "first_voucher_posted",
        "first_invoice_extracted",
    ):
        assert by_key[key]["completed"] is False
        assert "completed_at" not in by_key[key]


def test_consumed_enrollment_code_ticks_connector_installed(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    now = datetime.now(UTC)
    code = ConnectorEnrollmentCode(
        company_id=company.id,
        created_by=user.id,
        code_hash="a" * 64,
        consumed_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(code)
    db_session.commit()

    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["connector_installed"]["completed"] is True
    assert by_key["connector_installed"]["completed_at"].startswith(
        now.isoformat()[:19]
    )
    assert body["completed_count"] == 2


def test_unconsumed_enrollment_code_does_not_tick_connector_installed(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    now = datetime.now(UTC)
    code = ConnectorEnrollmentCode(
        company_id=company.id,
        created_by=user.id,
        code_hash="b" * 64,
        consumed_at=None,
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(code)
    db_session.commit()

    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["connector_installed"]["completed"] is False


def test_tally_synced_ledger_ticks_ledgers_synced(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    now = datetime.now(UTC)
    # A manually-created ledger should NOT tick the item.
    db_session.add(
        Ledger(
            company_id=company.id,
            name="Bank",
            name_normalized="bank",
            group_name="Bank Accounts",
            balance_type=BalanceType.Dr,
        )
    )
    db_session.commit()
    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["ledgers_synced"]["completed"] is False

    # Synced ledger flips it on.
    db_session.add(
        Ledger(
            company_id=company.id,
            name="Cash",
            name_normalized="cash",
            group_name="Cash-in-Hand",
            balance_type=BalanceType.Dr,
            tally_master_id="tally-cash-1",
            tally_synced_at=now,
        )
    )
    db_session.commit()
    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["ledgers_synced"]["completed"] is True


def test_posted_voucher_ticks_first_voucher_posted(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
    )
    cust = Ledger(
        company_id=company.id,
        name="Acme",
        name_normalized="acme",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    db_session.add_all([bank, cust])
    db_session.commit()

    _post_voucher(db_session, company.id, dr_ledger=bank, cr_ledger=cust)

    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["first_voucher_posted"]["completed"] is True
    assert "completed_at" in by_key["first_voucher_posted"]


def test_first_invoice_extracted_is_never_complete_in_phase_0(
    client: TestClient, db_session: Session
) -> None:
    user, company = _seed(db_session)
    body = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company)
    ).json()
    by_key = {i["key"]: i for i in body["items"]}
    assert by_key["first_invoice_extracted"]["completed"] is False
    assert "completed_at" not in by_key["first_invoice_extracted"]


def test_checklist_is_scoped_to_active_company(
    client: TestClient, db_session: Session
) -> None:
    # User belongs to two companies. Posting a voucher in company A
    # must not tick first_voucher_posted in company B.
    user = make_user(db_session)
    company_a = make_company(db_session, name="A Co")
    company_b = make_company(db_session, name="B Co")
    make_membership(db_session, user, company_a, role=CompanyRole.owner)
    make_membership(db_session, user, company_b, role=CompanyRole.owner)

    bank_a = Ledger(
        company_id=company_a.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        balance_type=BalanceType.Dr,
    )
    cust_a = Ledger(
        company_id=company_a.id,
        name="Acme",
        name_normalized="acme",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    db_session.add_all([bank_a, cust_a])
    db_session.commit()
    _post_voucher(db_session, company_a.id, dr_ledger=bank_a, cr_ledger=cust_a)

    body_a = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company_a)
    ).json()
    body_b = client.get(
        "/api/v1/onboarding/checklist", headers=_h(user, company_b)
    ).json()

    a_keys = {i["key"]: i for i in body_a["items"]}
    b_keys = {i["key"]: i for i in body_b["items"]}
    assert a_keys["first_voucher_posted"]["completed"] is True
    assert b_keys["first_voucher_posted"]["completed"] is False


def test_non_member_cannot_read_checklist(
    client: TestClient, db_session: Session
) -> None:
    # `user_x` is not a member of `company`; the tenancy dependency
    # should 404 (per TENANCY.md: 404 not 403 for non-membership).
    _, company = _seed(db_session)
    other_user = make_user(db_session, email="outsider@example.com")

    r = client.get(
        "/api/v1/onboarding/checklist", headers=_h(other_user, company)
    )
    assert r.status_code == 404, r.text
