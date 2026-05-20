"""Unit tests for `enqueue_voucher_post` branching (P0.58).

Three branches:

  1. `TAXMIND_SKIP_TALLY_DISPATCH=1` → return without dispatching.
  2. `CELERY_TASK_ALWAYS_EAGER=1` → route through `_enqueue_in_process`
     (the in-process asyncio.create_task path). The Celery `.delay()`
     path must NOT be touched in this mode — touching it would import
     the worker module and re-introduce the cross-process registry
     problem at module-import time.
  3. Neither env set → fall through to `post_voucher_to_tally.delay()`.

These are pure dispatch-routing tests; they intentionally don't drive
the asyncio task itself (covered by the integration test).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.services.tally import voucher_dispatcher


@pytest.fixture
def _clear_dispatch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip both env flags so each branch is exercised explicitly."""
    monkeypatch.delenv("TAXMIND_SKIP_TALLY_DISPATCH", raising=False)
    monkeypatch.delenv("CELERY_TASK_ALWAYS_EAGER", raising=False)


def test_skip_env_short_circuits(
    monkeypatch: pytest.MonkeyPatch, _clear_dispatch_env: None
) -> None:
    monkeypatch.setenv("TAXMIND_SKIP_TALLY_DISPATCH", "1")
    in_process_calls: list[dict] = []
    monkeypatch.setattr(
        voucher_dispatcher,
        "_enqueue_in_process",
        lambda **kw: in_process_calls.append(kw),
    )

    def _fail_import(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "Celery .delay() must not be reached when SKIP is set"
        )

    monkeypatch.setattr(
        "app.workers.posting_tasks.post_voucher_to_tally.delay",
        _fail_import,
    )

    voucher_dispatcher.enqueue_voucher_post(
        voucher_id=uuid4(),
        company_id=uuid4(),
        user_id=uuid4(),
        request_id=uuid4(),
    )

    assert in_process_calls == [], (
        "SKIP must short-circuit before the eager branch"
    )


def test_eager_env_routes_through_in_process(
    monkeypatch: pytest.MonkeyPatch, _clear_dispatch_env: None
) -> None:
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "1")
    in_process_calls: list[dict] = []
    monkeypatch.setattr(
        voucher_dispatcher,
        "_enqueue_in_process",
        lambda **kw: in_process_calls.append(kw),
    )

    def _fail_delay(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "Celery .delay() must not be touched in eager mode"
        )

    monkeypatch.setattr(
        "app.workers.posting_tasks.post_voucher_to_tally.delay",
        _fail_delay,
    )

    voucher_id = uuid4()
    company_id = uuid4()
    user_id = uuid4()
    request_id = uuid4()

    voucher_dispatcher.enqueue_voucher_post(
        voucher_id=voucher_id,
        company_id=company_id,
        user_id=user_id,
        request_id=request_id,
    )

    assert len(in_process_calls) == 1
    call = in_process_calls[0]
    assert call["voucher_id"] == voucher_id
    assert call["company_id"] == company_id
    assert call["user_id"] == user_id
    assert call["request_id"] == request_id


def test_worker_path_when_no_flags_set(
    monkeypatch: pytest.MonkeyPatch, _clear_dispatch_env: None
) -> None:
    delay_calls: list[dict] = []
    monkeypatch.setattr(
        "app.workers.posting_tasks.post_voucher_to_tally.delay",
        lambda **kw: delay_calls.append(kw),
    )

    def _fail_in_process(**_kw):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "in-process path must not be taken when eager flag is unset"
        )

    monkeypatch.setattr(
        voucher_dispatcher, "_enqueue_in_process", _fail_in_process
    )

    voucher_id = uuid4()
    company_id = uuid4()

    voucher_dispatcher.enqueue_voucher_post(
        voucher_id=voucher_id,
        company_id=company_id,
        user_id=None,
        request_id=uuid4(),
    )

    assert len(delay_calls) == 1
    call = delay_calls[0]
    assert call["voucher_id"] == str(voucher_id)
    assert call["company_id"] == str(company_id)
    assert call["user_id"] is None
