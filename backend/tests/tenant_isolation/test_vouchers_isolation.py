"""Tenant-isolation tests for /vouchers/ create (P0.18)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import Voucher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)

pytestmark = pytest.mark.tenant_isolation


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
        "Idempotency-Key": str(uuid4()),
    }


def test_user_cannot_create_voucher_in_other_company(
    client: TestClient, db_session: Session
) -> None:
    """User has no membership of B; sending X-Company-ID=B → 404."""
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    bank_b = Ledger(
        company_id=b.id, name="Bank B", name_normalized="bank b"
    )
    party_b = Ledger(
        company_id=b.id, name="Party B", name_normalized="party b"
    )
    db_session.add_all([bank_b, party_b])
    db_session.commit()

    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(u1, b),  # X-Company-ID=B but u1 isn't a member
        json={
            "voucher_type": "Receipt",
            "date": "2026-05-08",
            "total_amount": "100.00",
            "entries": [
                {
                    "ledger_id": str(bank_b.id),
                    "amount": "100.00",
                    "entry_type": "Dr",
                },
                {
                    "ledger_id": str(party_b.id),
                    "amount": "100.00",
                    "entry_type": "Cr",
                },
            ],
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "company_not_found"
    assert db_session.query(Voucher).count() == 0


def test_voucher_create_rejects_ledger_from_other_company(
    client: TestClient, db_session: Session
) -> None:
    """A ledger from a different company in the entries list → 404."""
    u1 = make_user(db_session, email="u1@ex.com")
    a = make_company(db_session, name="A")
    b = make_company(db_session, name="B")
    make_membership(db_session, u1, a, role=CompanyRole.owner)

    bank_a = Ledger(
        company_id=a.id, name="Bank A", name_normalized="bank a"
    )
    party_b = Ledger(
        company_id=b.id, name="Party B", name_normalized="party b"
    )
    db_session.add_all([bank_a, party_b])
    db_session.commit()

    r = client.post(
        "/api/v1/vouchers/",
        headers=_h(u1, a),
        json={
            "voucher_type": "Receipt",
            "date": "2026-05-08",
            "total_amount": "100.00",
            "entries": [
                {
                    "ledger_id": str(bank_a.id),
                    "amount": "100.00",
                    "entry_type": "Dr",
                },
                {
                    "ledger_id": str(party_b.id),  # cross-tenant!
                    "amount": "100.00",
                    "entry_type": "Cr",
                },
            ],
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ledger_not_found"
    # No voucher row created.
    assert db_session.query(Voucher).count() == 0
