"""P3.7 voucher identity regression tests (DB-backed).

Enforce the approved identity model on a migrated database:

  durable identity:   (company_id, tally_guid)  — partial unique index
  display-only:       voucher_number            — duplicates are legal
  dispatch identity:  tally_voucher_guid (REMOTEID) — unchanged

Real Tally evidence behind this: two distinct live Sales vouchers share
`VAC/25-26/222` across financial years (different native GUIDs), so
`(company_id, voucher_type, voucher_number)` is NOT a valid uniqueness
key (GATE 2/3/4).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models.voucher import Voucher, VoucherType
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company

pytestmark = pytest.mark.integration


def _voucher(company_id, *, number: str, tally_guid: str | None) -> Voucher:
    return Voucher(
        company_id=company_id,
        voucher_type=VoucherType.Sales,
        voucher_number=number,
        date=date(2026, 9, 12),
        total_amount=Decimal("1000.00"),
        tally_guid=tally_guid,
        source="tally_sync",
    )


def test_duplicate_company_type_number_with_different_tally_guid_coexist(
    db_session: Session,
) -> None:
    """Same (company_id, voucher_type, voucher_number), different tally_guid → OK."""
    company = make_company(db_session)
    db_session.add(
        _voucher(company.id, number="VAC/25-26/222", tally_guid="GUID-A")
    )
    db_session.add(
        _voucher(company.id, number="VAC/25-26/222", tally_guid="GUID-B")
    )
    db_session.commit()


def test_same_company_same_tally_guid_rejected(db_session: Session) -> None:
    """Same company + same tally_guid → MUST NOT create a duplicate."""
    company = make_company(db_session)
    db_session.add(
        _voucher(company.id, number="VAC/25-26/222", tally_guid="GUID-A")
    )
    db_session.commit()

    db_session.add(
        _voucher(company.id, number="VAC/25-26/999", tally_guid="GUID-A")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_different_companies_may_share_tally_guid(db_session: Session) -> None:
    """tally_guid uniqueness is scoped per company."""
    company_a = make_company(db_session, name="CoA")
    company_b = make_company(db_session, name="CoB")
    db_session.add(
        _voucher(company_a.id, number="VAC/25-26/222", tally_guid="SHARED-GUID")
    )
    db_session.add(
        _voucher(company_b.id, number="VAC/25-26/222", tally_guid="SHARED-GUID")
    )
    db_session.commit()


def test_null_tally_guid_rows_coexist(db_session: Session) -> None:
    """Manual vouchers (tally_guid NULL) can repeat numbers freely."""
    company = make_company(db_session)
    for _ in range(3):
        db_session.add(
            _voucher(company.id, number="R-1", tally_guid=None)
        )
    db_session.commit()


def test_tally_voucher_guid_semantics_unchanged(db_session: Session) -> None:
    """REMOTEID/dispatch identity flows independently of tally_guid."""
    company = make_company(db_session)
    v = _voucher(company.id, number="R-1", tally_guid=None)
    v.tally_voucher_guid = str(uuid4())  # REMOTEID back from Tally
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    assert v.tally_voucher_guid is not None
    assert v.tally_guid is None  # native GUID remains unset


def test_old_constraint_gone_and_new_index_present(db_session: Session) -> None:
    conn = db_session.connection()
    old = conn.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conname = 'uq_vouchers_company_number_type'"
        )
    ).fetchone()
    assert old is None

    row = conn.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'uq_vouchers_company_tally_guid'"
        )
    ).one()
    assert "UNIQUE" in row.indexdef
    assert "tally_guid IS NOT NULL" in row.indexdef
