"""Integration tests for BUG-005 step 3 — voucher post guard.

Verifies `check_ledgers_synced` and its two call sites:

  - API layer (`VoucherService.create`) — 422 + no voucher row created
  - Dispatcher layer (`dispatch_voucher_to_tally`) — defense-in-depth
    block, voucher stays at pending_tally_post, audit row emitted

Both layers are conditioned on `TAXMIND_SKIP_TALLY_DISPATCH` for
test-session convenience (matching the existing pattern at
`voucher_dispatcher.py:_enqueue_in_process`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.database import SessionLocal
from app.core.exceptions import LedgerNotSyncedToTally
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import Voucher, VoucherStatus
from app.services.tally.voucher_dispatcher import (
    check_ledgers_synced,
    dispatch_voucher_to_tally,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _setup_with_sync_states(
    db_session: Session,
    *,
    bank_synced: bool,
    party_synced: bool,
):  # type: ignore[no-untyped-def]
    """Build company + user + membership + two ledgers with chosen sync state."""
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(
        company_id=company.id,
        name="Bank",
        name_normalized="bank",
        group_name="Bank Accounts",
        tally_master_id="bank-guid" if bank_synced else None,
    )
    party = Ledger(
        company_id=company.id,
        name="Sharma",
        name_normalized="sharma",
        group_name="Sundry Debtors",
        tally_master_id="sharma-guid" if party_synced else None,
    )
    db_session.add_all([bank, party])
    db_session.commit()
    return user, company, bank, party


# ---------------------------------------------------------------------
# Unit tests on the helper itself
# ---------------------------------------------------------------------


def test_check_ledgers_synced_passes_when_all_synced(
    db_session: Session,
) -> None:
    _, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=True, party_synced=True
    )
    # Should not raise.
    check_ledgers_synced(
        db_session,
        ledger_ids=[bank.id, party.id],
        company_id=company.id,
    )


def test_check_ledgers_synced_raises_when_one_unsynced(
    db_session: Session,
) -> None:
    _, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=True, party_synced=False
    )
    with pytest.raises(LedgerNotSyncedToTally) as exc_info:
        check_ledgers_synced(
            db_session,
            ledger_ids=[bank.id, party.id],
            company_id=company.id,
        )
    exc = exc_info.value
    assert exc.code == "ledger_not_synced_to_tally"
    # Only the unsynced ledger (Sharma) appears in details.
    assert exc.details["unsynced_ledger_names"] == ["Sharma"]
    assert exc.details["unsynced_ledger_ids"] == [str(party.id)]
    # Bank (synced) does NOT appear.
    assert "Bank" not in exc.details["unsynced_ledger_names"]
    # Remediation surface is present and gives a concrete operator action
    # (the API endpoint to call). Message itself references sync_masters
    # as the conceptual fix.
    assert "sync_masters" in exc.message
    assert "/api/v1/connector/sync/" in exc.details["remediation"]


def test_check_ledgers_synced_raises_when_multiple_unsynced(
    db_session: Session,
) -> None:
    _, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=False, party_synced=False
    )
    with pytest.raises(LedgerNotSyncedToTally) as exc_info:
        check_ledgers_synced(
            db_session,
            ledger_ids=[bank.id, party.id],
            company_id=company.id,
        )
    exc = exc_info.value
    names = set(exc.details["unsynced_ledger_names"])
    assert names == {"Bank", "Sharma"}
    assert len(exc.details["unsynced_ledger_ids"]) == 2
    # Plural count surfaces in the message.
    assert "and 1 other ledger(s)" in exc.message


# ---------------------------------------------------------------------
# API-layer integration (full HTTP path via TestClient)
# ---------------------------------------------------------------------


def _headers(user, company, *, idem: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
        "Idempotency-Key": idem,
    }


def _payload(bank, party):  # type: ignore[no-untyped-def]
    return {
        "voucher_type": "Receipt",
        "date": "2026-05-08",
        "narration": "Payment",
        "reference": "REF1",
        "total_amount": "100.00",
        "entries": [
            {"ledger_id": str(bank.id), "amount": "100.00", "entry_type": "Dr"},
            {"ledger_id": str(party.id), "amount": "100.00", "entry_type": "Cr"},
        ],
        "gst_applicable": False,
    }


def test_api_create_voucher_returns_422_when_ledger_unsynced(
    db_session: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API guard active: POST /vouchers/ with unsynced ledger → 422,
    no voucher row created."""
    # Opt back into the guard (conftest defaults SKIP=1).
    monkeypatch.delenv("TAXMIND_SKIP_TALLY_DISPATCH", raising=False)

    user, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=True, party_synced=False
    )

    resp = client.post(
        "/api/v1/vouchers/",
        json=_payload(bank, party),
        headers=_headers(user, company, idem=str(uuid4())),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "ledger_not_synced_to_tally"
    assert body["error"]["details"]["unsynced_ledger_names"] == ["Sharma"]
    assert body["error"]["details"]["unsynced_ledger_ids"] == [str(party.id)]
    assert "remediation" in body["error"]["details"]

    # No voucher row was created.
    voucher_count = (
        db_session.query(Voucher)
        .filter(Voucher.company_id == company.id)
        .count()
    )
    assert voucher_count == 0


def test_api_create_voucher_succeeds_when_skip_flag_set(
    db_session: Session,
    client: TestClient,
) -> None:
    """API guard skipped under SKIP=1 (conftest default): POST /vouchers/
    succeeds even with all ledgers unsynced. Preserves existing
    test-session behavior for the ~15 other voucher test files that
    construct Ledger rows without tally_master_id."""
    # SKIP=1 is the conftest default — no monkeypatch needed.
    user, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=False, party_synced=False
    )
    resp = client.post(
        "/api/v1/vouchers/",
        json=_payload(bank, party),
        headers=_headers(user, company, idem=str(uuid4())),
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------
# Dispatcher-layer integration (defense-in-depth)
# ---------------------------------------------------------------------


async def _run_dispatch(
    voucher_id, company_id, user_id, request_id
):  # type: ignore[no-untyped-def]
    """Call dispatch_voucher_to_tally directly from a coroutine.

    Opens its own session because the dispatcher commits independently
    of any request-scoped session and the test needs to observe the
    committed state afterward.

    Mirrors the commit-before-reraise pattern in
    `voucher_dispatcher._enqueue_in_process`: when the dispatcher
    raises LedgerNotSyncedToTally, the audit row + tally_last_error
    update sit in the session and would be lost if we just let the
    exception propagate. Commit first, then re-raise.
    """
    db = SessionLocal()
    try:
        await dispatch_voucher_to_tally(
            db=db,
            voucher_id=voucher_id,
            company_id=company_id,
            user_id=user_id,
            request_id=request_id,
        )
        db.commit()
    except LedgerNotSyncedToTally:
        db.commit()
        raise
    finally:
        db.close()


def test_dispatcher_blocks_voucher_and_emits_audit(
    db_session: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: voucher created under SKIP=1 (bypasses API guard),
    then dispatched with SKIP unset → dispatcher guard fires, raises
    LedgerNotSyncedToTally, emits voucher.tally_post_blocked audit,
    populates tally_last_error, voucher stays pending_tally_post,
    tally_post_attempts NOT incremented."""
    import asyncio

    # Create the voucher with SKIP=1 (API guard inactive).
    user, company, bank, party = _setup_with_sync_states(
        db_session, bank_synced=False, party_synced=False
    )
    create_resp = client.post(
        "/api/v1/vouchers/",
        json=_payload(bank, party),
        headers=_headers(user, company, idem=str(uuid4())),
    )
    assert create_resp.status_code == 201
    voucher_id = create_resp.json()["id"]

    # Now flip the flag and call the dispatcher directly.
    monkeypatch.delenv("TAXMIND_SKIP_TALLY_DISPATCH", raising=False)

    with pytest.raises(LedgerNotSyncedToTally):
        asyncio.run(
            _run_dispatch(
                voucher_id=voucher_id,
                company_id=company.id,
                user_id=user.id,
                request_id=uuid4(),
            )
        )

    # Audit row reflects the block.
    db_session.expire_all()  # re-read from the DB (other session committed)
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "voucher.tally_post_blocked",
            AuditLog.entity_id == voucher_id,
        )
        .one()
    )
    assert audit.new_value["code"] == "ledger_not_synced_to_tally"
    assert "Bank" in audit.new_value["unsynced_ledger_names"]
    assert "Sharma" in audit.new_value["unsynced_ledger_names"]
    assert "remediation" in audit.new_value

    # Voucher row: status preserved, tally_last_error populated,
    # attempts NOT incremented.
    voucher = (
        db_session.query(Voucher).filter(Voucher.id == voucher_id).one()
    )
    assert voucher.status == VoucherStatus.pending_tally_post
    assert voucher.tally_last_error is not None
    assert "not yet synced to Tally" in voucher.tally_last_error
    assert voucher.tally_post_attempts == 0  # pre-flight reject, not a Tally attempt
