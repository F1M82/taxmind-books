"""Integration tests for POST /api/v1/vouchers/ — per-type rules (P0.36).

For each of the 8 Tally voucher types this file exercises:
- the happy path (well-formed entries on the right ledger groups), and
- the type-specific rejection (the rule documented in API.md fires
  `voucher_type_rule_violation`).

Group-classification rules live in
`backend/app/services/voucher_groups.py`; the validator is
`VoucherService._validate_type_rules`.
"""

from __future__ import annotations

from uuid import uuid4

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

# ---------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------


def _h(user, company, *, idem: str | None = None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    h = {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


def _seed(db: Session):  # type: ignore[no-untyped-def]
    """One user + one company; caller adds the ledgers it needs."""
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.owner)
    return user, company


def _ledger(
    db: Session,
    company,  # type: ignore[no-untyped-def]
    *,
    name: str,
    group: str | None,
) -> Ledger:
    led = Ledger(
        company_id=company.id,
        name=name,
        name_normalized=name.lower(),
        group_name=group,
    )
    db.add(led)
    db.flush()
    return led


def _payload(
    *,
    voucher_type: str,
    dr_ledger_id: str,
    cr_ledger_id: str,
    amount: str = "1000.00",
    extra_entries: list[dict[str, str]] | None = None,
) -> dict:
    entries = [
        {"ledger_id": dr_ledger_id, "amount": amount, "entry_type": "Dr"},
        {"ledger_id": cr_ledger_id, "amount": amount, "entry_type": "Cr"},
    ]
    if extra_entries:
        entries.extend(extra_entries)
    return {
        "voucher_type": voucher_type,
        "date": "2026-05-08",
        "narration": f"{voucher_type} test",
        "total_amount": amount,
        "entries": entries,
        "gst_applicable": False,
    }


def _post(client: TestClient, user, company, body: dict):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/vouchers/",
        headers=_h(user, company, idem=str(uuid4())),
        json=body,
    )


# ---------------------------------------------------------------------
# Receipt — Dr Bank/Cash, Cr party
# ---------------------------------------------------------------------


class TestReceipt:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        party = _ledger(
            db_session, company, name="Sharma", group="Sundry Debtors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Receipt",
                dr_ledger_id=str(bank.id),
                cr_ledger_id=str(party.id),
            ),
        )
        assert r.status_code == 201, r.json()
        assert r.json()["voucher_type"] == "Receipt"

    def test_rejects_no_bank_or_cash_dr(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Sundry Debtors Dr + Sales Cr — no Bank/Cash on Dr side → 422."""
        user, company = _seed(db_session)
        debtor = _ledger(
            db_session, company, name="X", group="Sundry Debtors"
        )
        sales = _ledger(
            db_session, company, name="Sales A/c", group="Sales Accounts"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Receipt",
                dr_ledger_id=str(debtor.id),
                cr_ledger_id=str(sales.id),
            ),
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "voucher_type_rule_violation"
        assert (
            r.json()["error"]["details"]["rule"] == "requires_bank_or_cash_dr"
        )
        assert db_session.query(Voucher).count() == 0


# ---------------------------------------------------------------------
# Payment — Cr Bank/Cash, Dr party/expense
# ---------------------------------------------------------------------


class TestPayment:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        creditor = _ledger(
            db_session, company, name="Acme Vendor", group="Sundry Creditors"
        )
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Payment",
                dr_ledger_id=str(creditor.id),
                cr_ledger_id=str(bank.id),
            ),
        )
        assert r.status_code == 201, r.json()
        assert r.json()["voucher_type"] == "Payment"

    def test_rejects_no_bank_or_cash_cr(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        expense = _ledger(
            db_session, company, name="Rent", group="Indirect Expenses"
        )
        creditor = _ledger(
            db_session, company, name="Landlord", group="Sundry Creditors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Payment",
                dr_ledger_id=str(expense.id),
                cr_ledger_id=str(creditor.id),
            ),
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "voucher_type_rule_violation"
        assert (
            r.json()["error"]["details"]["rule"] == "requires_bank_or_cash_cr"
        )


# ---------------------------------------------------------------------
# Sales — at least one Sundry Debtors entry
# ---------------------------------------------------------------------


class TestSales:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        customer = _ledger(
            db_session, company, name="Patel & Co", group="Sundry Debtors"
        )
        sales = _ledger(
            db_session, company, name="Sales A/c", group="Sales Accounts"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Sales",
                dr_ledger_id=str(customer.id),
                cr_ledger_id=str(sales.id),
            ),
        )
        assert r.status_code == 201, r.json()

    def test_rejects_without_sundry_debtors(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        sales = _ledger(
            db_session, company, name="Sales A/c", group="Sales Accounts"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Sales",
                dr_ledger_id=str(bank.id),
                cr_ledger_id=str(sales.id),
            ),
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "voucher_type_rule_violation"
        assert (
            r.json()["error"]["details"]["rule"]
            == "requires_sundry_debtors"
        )


# ---------------------------------------------------------------------
# Purchase — at least one Sundry Creditors entry
# ---------------------------------------------------------------------


class TestPurchase:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        purchases = _ledger(
            db_session,
            company,
            name="Purchase A/c",
            group="Purchase Accounts",
        )
        supplier = _ledger(
            db_session, company, name="Wholesale Co", group="Sundry Creditors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Purchase",
                dr_ledger_id=str(purchases.id),
                cr_ledger_id=str(supplier.id),
            ),
        )
        assert r.status_code == 201, r.json()

    def test_rejects_without_sundry_creditors(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        purchases = _ledger(
            db_session,
            company,
            name="Purchase A/c",
            group="Purchase Accounts",
        )
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Purchase",
                dr_ledger_id=str(purchases.id),
                cr_ledger_id=str(bank.id),
            ),
        )
        assert r.status_code == 422
        assert (
            r.json()["error"]["details"]["rule"]
            == "requires_sundry_creditors"
        )


# ---------------------------------------------------------------------
# Journal — must NOT touch Bank/Cash
# ---------------------------------------------------------------------


class TestJournal:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        depr = _ledger(
            db_session,
            company,
            name="Depreciation",
            group="Indirect Expenses",
        )
        accum = _ledger(
            db_session,
            company,
            name="Accum. Depreciation",
            group="Fixed Assets",
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Journal",
                dr_ledger_id=str(depr.id),
                cr_ledger_id=str(accum.id),
            ),
        )
        assert r.status_code == 201, r.json()

    def test_rejects_with_bank_or_cash(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        expense = _ledger(
            db_session, company, name="Rent", group="Indirect Expenses"
        )
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Journal",
                dr_ledger_id=str(expense.id),
                cr_ledger_id=str(bank.id),
            ),
        )
        assert r.status_code == 422
        assert r.json()["error"]["details"]["rule"] == "no_bank_or_cash"


# ---------------------------------------------------------------------
# Contra — only Bank/Cash on every entry
# ---------------------------------------------------------------------


class TestContra:
    def test_happy_bank_to_cash(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        cash = _ledger(
            db_session, company, name="Petty Cash", group="Cash-in-hand"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Contra",
                dr_ledger_id=str(cash.id),
                cr_ledger_id=str(bank.id),
            ),
        )
        assert r.status_code == 201, r.json()

    def test_rejects_party_ledger(
        self, client: TestClient, db_session: Session
    ) -> None:
        """Contra with a Sundry Debtors entry → 422 all_bank_or_cash."""
        user, company = _seed(db_session)
        bank = _ledger(db_session, company, name="HDFC", group="Bank Accounts")
        debtor = _ledger(
            db_session, company, name="X", group="Sundry Debtors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Contra",
                dr_ledger_id=str(debtor.id),
                cr_ledger_id=str(bank.id),
            ),
        )
        assert r.status_code == 422
        details = r.json()["error"]["details"]
        assert details["rule"] == "all_bank_or_cash"
        assert str(debtor.id) in details["offending_ledger_ids"]


# ---------------------------------------------------------------------
# Debit Note — at least one Sundry Creditors (purchase return)
# ---------------------------------------------------------------------


class TestDebitNote:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        supplier = _ledger(
            db_session, company, name="Wholesale Co", group="Sundry Creditors"
        )
        purchase_return = _ledger(
            db_session,
            company,
            name="Purchase Return",
            group="Purchase Accounts",
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Debit Note",
                dr_ledger_id=str(supplier.id),
                cr_ledger_id=str(purchase_return.id),
            ),
        )
        assert r.status_code == 201, r.json()
        assert r.json()["voucher_type"] == "Debit Note"

    def test_rejects_without_sundry_creditors(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        debtor = _ledger(
            db_session, company, name="Customer X", group="Sundry Debtors"
        )
        sales = _ledger(
            db_session, company, name="Sales A/c", group="Sales Accounts"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Debit Note",
                dr_ledger_id=str(debtor.id),
                cr_ledger_id=str(sales.id),
            ),
        )
        assert r.status_code == 422
        assert (
            r.json()["error"]["details"]["rule"]
            == "requires_sundry_creditors"
        )


# ---------------------------------------------------------------------
# Credit Note — at least one Sundry Debtors (sales return)
# ---------------------------------------------------------------------


class TestCreditNote:
    def test_happy(self, client: TestClient, db_session: Session) -> None:
        user, company = _seed(db_session)
        sales_return = _ledger(
            db_session, company, name="Sales Return", group="Sales Accounts"
        )
        customer = _ledger(
            db_session, company, name="Patel & Co", group="Sundry Debtors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Credit Note",
                dr_ledger_id=str(sales_return.id),
                cr_ledger_id=str(customer.id),
            ),
        )
        assert r.status_code == 201, r.json()
        assert r.json()["voucher_type"] == "Credit Note"

    def test_rejects_without_sundry_debtors(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, company = _seed(db_session)
        purchases = _ledger(
            db_session,
            company,
            name="Purchase A/c",
            group="Purchase Accounts",
        )
        supplier = _ledger(
            db_session, company, name="Vendor", group="Sundry Creditors"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Credit Note",
                dr_ledger_id=str(purchases.id),
                cr_ledger_id=str(supplier.id),
            ),
        )
        assert r.status_code == 422
        assert (
            r.json()["error"]["details"]["rule"] == "requires_sundry_debtors"
        )


# ---------------------------------------------------------------------
# Cross-type smoke: groups missing on the ledger don't slip past
# ---------------------------------------------------------------------


class TestUngroupedLedgerRejected:
    def test_sales_with_ungrouped_party_is_rejected(
        self, client: TestClient, db_session: Session
    ) -> None:
        """A ledger with no `group_name` does not satisfy any rule —
        the validator must NOT silently accept None."""
        user, company = _seed(db_session)
        ungrouped = _ledger(
            db_session, company, name="Mystery Party", group=None
        )
        sales = _ledger(
            db_session, company, name="Sales A/c", group="Sales Accounts"
        )
        db_session.commit()
        r = _post(
            client,
            user,
            company,
            _payload(
                voucher_type="Sales",
                dr_ledger_id=str(ungrouped.id),
                cr_ledger_id=str(sales.id),
            ),
        )
        assert r.status_code == 422
        assert (
            r.json()["error"]["details"]["rule"] == "requires_sundry_debtors"
        )
