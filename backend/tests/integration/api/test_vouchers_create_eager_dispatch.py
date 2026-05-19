"""Integration test for POST /api/v1/vouchers/ under eager-dispatch (P0.58).

When `CELERY_TASK_ALWAYS_EAGER=1` is set, `enqueue_voucher_post` routes
through `_enqueue_in_process` (asyncio.create_task on the current event
loop) instead of `post_voucher_to_tally.delay()`. The API request path
must:

  1. Route through the in-process branch (no Celery `.delay()` call).
  2. Return 201 promptly — the dispatch is fire-and-forget, not
     synchronous. Latency budget: < 2 seconds end-to-end, even when
     the dispatch coroutine itself simulates a slow Tally round-trip.

The actual dispatcher behavior is covered by tests in
`tests/integration/workers/test_posting_task.py` (which exercise
`dispatch_voucher_to_tally` directly). End-to-end real-connector
verification is the §7.5b live re-run.

Background: BUG-Books-003 / P0.58 — see
`memory/bug_books_003_worker_registry_process_local.md`.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.services.tally import voucher_dispatcher
from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _headers(user, company, *, idem: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Authorization": f"Bearer {issue_token(user)}",
        "X-Company-ID": str(company.id),
        "Idempotency-Key": idem,
    }


def _setup(db_session: Session):  # type: ignore[no-untyped-def]
    user = make_user(db_session)
    company = make_company(db_session)
    make_membership(db_session, user, company, role=CompanyRole.owner)
    bank = Ledger(
        company_id=company.id,
        name="HDFC Bank",
        name_normalized="hdfc bank",
        group_name="Bank Accounts",
    )
    party = Ledger(
        company_id=company.id,
        name="Xyz Ltd",
        name_normalized="xyz ltd",
        group_name="Sundry Debtors",
    )
    db_session.add_all([bank, party])
    db_session.commit()
    return user, company, bank, party


def test_eager_dispatch_routes_in_process_and_returns_under_2s(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /vouchers/ with eager flag set must take the in-process branch.

    Verifies the routing decision AND that the response is not blocked
    on the dispatch's async work. The dispatcher itself is stubbed with
    an async coroutine that simulates a slow Tally round-trip via
    `asyncio.sleep` — if `_enqueue_in_process` accidentally `await`ed
    that coroutine inline instead of scheduling it as a task, the POST
    latency would exceed the budget.
    """
    # Conftest defaults: SKIP=1, EAGER unset. We want EAGER=1, SKIP unset.
    monkeypatch.delenv("TAXMIND_SKIP_TALLY_DISPATCH", raising=False)
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "1")

    # Stub the async dispatcher with a *slow* coroutine. The
    # `_enqueue_in_process` -> `_drive()` wrapper calls this via
    # `await dispatch_voucher_to_tally(...)`. If `_drive` is scheduled
    # via `loop.create_task` (correct), this sleep runs in the
    # background and the POST is unaffected. If anything synchronously
    # awaits `_drive`, the POST would be blocked for 3 seconds.
    dispatch_called = asyncio.Event()
    dispatch_args: list[dict] = []

    async def _slow_dispatch_stub(**kwargs):  # type: ignore[no-untyped-def]
        dispatch_args.append(kwargs)
        await asyncio.sleep(3.0)
        dispatch_called.set()
        # Return shape doesn't matter; the stub's caller (_drive) just
        # commits and exits on exception or success.
        return {"status": "posted"}

    monkeypatch.setattr(
        voucher_dispatcher,
        "dispatch_voucher_to_tally",
        _slow_dispatch_stub,
    )

    # Fail the test loudly if the worker `.delay()` path is reached —
    # under eager mode it must NOT be touched.
    def _fail_delay(**_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "post_voucher_to_tally.delay() must not be called when "
            "CELERY_TASK_ALWAYS_EAGER=1"
        )

    monkeypatch.setattr(
        "app.workers.posting_tasks.post_voucher_to_tally.delay",
        _fail_delay,
    )

    user, company, bank, party = _setup(db_session)
    payload = {
        "voucher_type": "Sales",
        "date": "2026-05-19",
        "narration": "P0.58 eager-dispatch wiring test",
        "total_amount": "100.00",
        "entries": [
            {
                "ledger_id": str(party.id),
                "amount": "100.00",
                "entry_type": "Dr",
                "narration": "Xyz Ltd Dr",
            },
            {
                "ledger_id": str(bank.id),
                "amount": "100.00",
                "entry_type": "Cr",
                "narration": "Sales Cr",
            },
        ],
    }

    started = time.perf_counter()
    r = client.post(
        "/api/v1/vouchers/",
        json=payload,
        headers=_headers(user, company, idem=str(uuid4())),
    )
    elapsed = time.perf_counter() - started

    assert r.status_code == 201, (
        f"expected 201, got {r.status_code}: {r.text}"
    )
    assert r.json()["status"] == "pending_tally_post"
    assert Decimal(r.json()["total_amount"]) == Decimal("100.00")

    # Latency budget: the dispatcher's stubbed `asyncio.sleep(3.0)`
    # must not have blocked the response. 2 seconds is generous —
    # the actual non-blocking path should be << 1 s.
    assert elapsed < 2.0, (
        f"POST blocked on dispatch: took {elapsed:.2f}s "
        f"(budget 2.0s, stub sleep 3.0s — should not have awaited)"
    )

    # Routing proof: the eager branch was taken (the stub was called
    # by `_drive` inside the scheduled task). The dispatch may still
    # be running when we reach this assertion — but the stub's
    # `dispatch_args` is appended before the sleep, so it's already
    # populated.
    assert len(dispatch_args) == 1
    assert dispatch_args[0]["company_id"] == company.id
