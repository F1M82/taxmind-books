"""Lifespan wiring for the BUG-002 periodic re-enqueue sweep.

`create_app` installs a lifespan that starts the sweep loop only when
`TALLY_REENQUEUE_SWEEP_ENABLED` and NOT `TAXMIND_SKIP_TALLY_DISPATCH`,
and cancels it cleanly on shutdown. These tests stub the loop itself
(the core is covered in test_voucher_reenqueue.py) and assert the
start/stop wiring — the one piece not exercised by the integration
`client` fixture, which runs with the sweep disabled.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.main as main_mod
import pytest


def _settings(*, enabled: bool, skip: bool = False, interval: int = 1):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        TALLY_REENQUEUE_SWEEP_ENABLED=enabled,
        TAXMIND_SKIP_TALLY_DISPATCH=skip,
        TALLY_REENQUEUE_SWEEP_INTERVAL_SECONDS=interval,
    )


async def test_lifespan_starts_and_cancels_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _stub_loop(interval_seconds: int) -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(main_mod, "_reenqueue_sweep_loop", _stub_loop)

    lifespan = main_mod._build_lifespan(_settings(enabled=True))
    async with lifespan(None):  # type: ignore[arg-type]
        await asyncio.wait_for(started.wait(), timeout=1.0)

    # Shutdown must have cancelled the background task.
    assert cancelled.is_set()


async def test_lifespan_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _stub_loop(interval_seconds: int) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(main_mod, "_reenqueue_sweep_loop", _stub_loop)

    lifespan = main_mod._build_lifespan(_settings(enabled=False))
    async with lifespan(None):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

    assert called is False


async def test_lifespan_noop_when_dispatch_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even enabled, the sweep must not run when Tally dispatch is skipped."""
    called = False

    async def _stub_loop(interval_seconds: int) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(main_mod, "_reenqueue_sweep_loop", _stub_loop)

    lifespan = main_mod._build_lifespan(_settings(enabled=True, skip=True))
    async with lifespan(None):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

    assert called is False
