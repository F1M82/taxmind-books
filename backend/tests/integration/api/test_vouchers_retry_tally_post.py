"""BUG-Books-002: operator-triggered Tally re-post.

POST /api/v1/vouchers/{id}/retry-tally-post is the manual counterpart to
the automatic retryable-class re-enqueue. It re-dispatches ANY stranded
`pending_tally_post` voucher — including the rejection- and unsynced-class
strands the auto-sweep deliberately skips — after an operator has fixed
the underlying cause.

Behavior pinned here:
  * 404 when the voucher doesn't exist;
  * 409 when the voucher isn't awaiting a Tally post (e.g. already posted);
  * success → status flips to `posted`, with a `voucher.tally_post_retry_requested`
    audit (who asked) preceding the dispatch's `voucher.posted_to_tally`;
  * Tally rejects again → 200 with status still `pending_tally_post` and
    `tally_last_error` set (outcome on the row, not an HTTP error);
  * connector offline → same (200, still pending).

The registry is faked (no live Tally). Conftest's default
`TAXMIND_SKIP_TALLY_DISPATCH=1` skips the dispatcher's ledger guard, so
`send_command` runs against the fake registry.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
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
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)

_RETRY = "voucher.tally_post_retry_requested"
_POSTED = "voucher.posted_to_tally"
_FAILED = "voucher.tally_post_failed"


def _h(user, company) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
    }


def _setup(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.owner)
    party = Ledger(
        company_id=company.id,
        name="Xyz Ltd",
        name_normalized="xyz ltd",
        group_name="Sundry Debtors",
        tally_master_id="guid-xyz",
    )
    sales = Ledger(
        company_id=company.id,
        name="Sales",
        name_normalized="sales",
        group_name="Sales Accounts",
        tally_master_id="guid-sales",
    )
    db.add_all([party, sales])
    db.commit()
    return user, company, party, sales


def _voucher(
    db: Session,
    company,  # type: ignore[no-untyped-def]
    party: Ledger,
    sales: Ledger,
    *,
    status: VoucherStatus = VoucherStatus.pending_tally_post,
    attempts: int = 1,
    last_error: str | None = "Ledger 'Sales' does not exist!",
) -> Voucher:
    v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Sales,
        date=date(2026, 7, 21),
        narration="strand",
        total_amount=Decimal("100.00"),
        status=status,
        source="manual",
        tally_post_attempts=attempts,
        tally_last_error=last_error,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=party.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=sales.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db.commit()
    return v


class _FakeRegistry:
    def __init__(self, *, outcome: str) -> None:
        self.outcome = outcome
        self.idempotency_keys: list[str] = []

    async def send_command(
        self,
        *,
        company_id,  # type: ignore[no-untyped-def]
        command: str,
        args: dict,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> dict:
        self.idempotency_keys.append(idempotency_key)
        if self.outcome == "offline":
            from app.services.tally.connector_registry import ConnectorOffline

            raise ConnectorOffline("no active connector")
        if self.outcome == "rejected":
            return {
                "status": "error",
                "retryable": False,
                "error": {
                    "code": "TallyImportRejected",
                    "message": "Ledger 'Sales' does not exist!",
                },
            }
        return {
            "status": "success",
            "result": {"tally_voucher_guid": "guid-ok"},
            "duration_ms": 3,
        }


def _patch_registry(monkeypatch: pytest.MonkeyPatch, reg: _FakeRegistry) -> None:
    monkeypatch.setattr(
        "app.services.tally.connector_registry.get_registry", lambda: reg
    )


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------


def test_retry_missing_voucher_404(
    client: TestClient, db_session: Session
) -> None:
    user, company, _, _ = _setup(db_session)
    r = client.post(
        f"/api/v1/vouchers/{uuid4()}/retry-tally-post",
        headers=_h(user, company),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "voucher_not_found"


def test_retry_non_pending_conflict_409(
    client: TestClient, db_session: Session
) -> None:
    user, company, party, sales = _setup(db_session)
    v = _voucher(
        db_session, company, party, sales, status=VoucherStatus.posted
    )
    r = client.post(
        f"/api/v1/vouchers/{v.id}/retry-tally-post",
        headers=_h(user, company),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert r.json()["error"]["details"]["status"] == "posted"


# ---------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------


def test_retry_success_marks_posted(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, company, party, sales = _setup(db_session)
    v = _voucher(db_session, company, party, sales)
    reg = _FakeRegistry(outcome="success")
    _patch_registry(monkeypatch, reg)

    r = client.post(
        f"/api/v1/vouchers/{v.id}/retry-tally-post",
        headers=_h(user, company),
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "posted"
    # Re-dispatch used the voucher id as idempotency key (no double-post).
    assert reg.idempotency_keys == [str(v.id)]

    db_session.expire_all()
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.posted
    assert refreshed.tally_posted_at is not None
    assert refreshed.tally_last_error is None

    actions = {
        a.action
        for a in db_session.query(AuditLog)
        .filter(AuditLog.entity_id == v.id)
        .all()
    }
    assert _RETRY in actions
    assert _POSTED in actions


def test_retry_rejection_stays_pending(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, company, party, sales = _setup(db_session)
    v = _voucher(db_session, company, party, sales, attempts=1)
    reg = _FakeRegistry(outcome="rejected")
    _patch_registry(monkeypatch, reg)

    r = client.post(
        f"/api/v1/vouchers/{v.id}/retry-tally-post",
        headers=_h(user, company),
    )

    # Retry was performed; the outcome (still failing) is on the row, not
    # an HTTP error.
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_tally_post"

    db_session.expire_all()
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.pending_tally_post
    assert refreshed.tally_post_attempts == 2  # dispatcher incremented
    assert "does not exist" in (refreshed.tally_last_error or "")

    actions = {
        a.action
        for a in db_session.query(AuditLog)
        .filter(AuditLog.entity_id == v.id)
        .all()
    }
    assert _RETRY in actions
    assert _FAILED in actions


def test_retry_connector_offline_stays_pending(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, company, party, sales = _setup(db_session)
    v = _voucher(db_session, company, party, sales)
    reg = _FakeRegistry(outcome="offline")
    _patch_registry(monkeypatch, reg)

    r = client.post(
        f"/api/v1/vouchers/{v.id}/retry-tally-post",
        headers=_h(user, company),
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_tally_post"
    db_session.expire_all()
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.pending_tally_post
