"""Auto-sync-on-connect (app/api/v1/connector_ws.py::_drive_auto_sync).

"Launch the connector, the app has current data" -- on every `register`
and every `tally_company_changed` event, pull masters for whichever
authorized company matches the Tally company currently open on that PC,
with no manual sync trigger required. Drives `_drive_auto_sync` directly
against a fake connection (send_command stubbed) rather than the full WS
transport -- see test_connector_ws.py's own note that command-dispatch
round-trips are hard to drive through the sync TestClient.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from app.api.v1.connector_ws import (
    _drive_auto_sync,
    _schedule_auto_sync_on_connector_up,
)
from app.models.ledger import Ledger
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company

GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"


class _FakeConn:
    def __init__(
        self,
        *,
        authorized_company_ids: set,  # type: ignore[type-arg]
        active_result: dict[str, Any],
        sync_result: dict[str, Any] | None = None,
        active_raises: bool = False,
        sync_raises: bool = False,
    ) -> None:
        self.connector_id = uuid4()
        self.authorized_company_ids = authorized_company_ids
        self._active_result = active_result
        self._sync_result = sync_result
        self._active_raises = active_raises
        self._sync_raises = sync_raises
        self.calls: list[str] = []

    async def send_command(  # type: ignore[no-untyped-def]
        self, *, command, args, company_id=None, timeout_seconds=30, idempotency_key=None
    ):
        self.calls.append(command)
        if command == "get_active_tally_company":
            if self._active_raises:
                raise RuntimeError("simulated transport failure")
            return self._active_result
        if command == "sync_masters":
            if self._sync_raises:
                raise RuntimeError("simulated transport failure")
            assert self._sync_result is not None
            return self._sync_result
        raise AssertionError(f"unexpected command {command!r}")


def _sync_success(*, ledger_name: str = "Cash", master_id: str = "tid-1") -> dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "company": {"guid": GUID, "name": "Test Co (Tally)"},
            "ledgers": [
                {
                    "name": ledger_name,
                    "group_name": "Cash-in-hand",
                    "gstin": None,
                    "master_id": master_id,
                }
            ],
            "groups": [],
        },
    }


@pytest.mark.asyncio
async def test_auto_sync_persists_ledgers_for_the_matching_company(
    db_session: Session,
) -> None:
    company = make_company(db_session, tally_master_id=GUID)
    conn = _FakeConn(
        authorized_company_ids={company.id},
        active_result={
            "status": "success",
            "result": {"tally_company_guid": GUID},
        },
        sync_result=_sync_success(),
    )

    await _drive_auto_sync(conn)

    assert conn.calls == ["get_active_tally_company", "sync_masters"]
    ledger = (
        db_session.query(Ledger)
        .filter(Ledger.company_id == company.id, Ledger.name == "Cash")
        .one()
    )
    assert ledger.tally_master_id == "tid-1"


@pytest.mark.asyncio
async def test_auto_sync_is_a_noop_when_no_company_matches_the_active_guid(
    db_session: Session,
) -> None:
    company = make_company(db_session, tally_master_id="some-other-guid")
    conn = _FakeConn(
        authorized_company_ids={company.id},
        active_result={
            "status": "success",
            "result": {"tally_company_guid": GUID},
        },
    )

    await _drive_auto_sync(conn)

    assert conn.calls == ["get_active_tally_company"]  # never reached sync_masters


@pytest.mark.asyncio
async def test_auto_sync_never_syncs_a_company_outside_authorized_ids(
    db_session: Session,
) -> None:
    # A company mapped to the active GUID exists, but this connector was
    # never bound to it -- tenant-safety: must not sync across the
    # authorization boundary just because the GUID happens to match.
    make_company(db_session, tally_master_id=GUID)
    conn = _FakeConn(
        authorized_company_ids=set(),  # not authorized for anything
        active_result={
            "status": "success",
            "result": {"tally_company_guid": GUID},
        },
    )

    await _drive_auto_sync(conn)

    assert conn.calls == ["get_active_tally_company"]


@pytest.mark.asyncio
async def test_auto_sync_swallows_active_company_lookup_failure(
    db_session: Session,
) -> None:
    company = make_company(db_session, tally_master_id=GUID)
    conn = _FakeConn(
        authorized_company_ids={company.id},
        active_result={},
        active_raises=True,
    )

    await _drive_auto_sync(conn)  # must not raise

    assert conn.calls == ["get_active_tally_company"]


@pytest.mark.asyncio
async def test_auto_sync_swallows_sync_masters_dispatch_failure(
    db_session: Session,
) -> None:
    company = make_company(db_session, tally_master_id=GUID)
    conn = _FakeConn(
        authorized_company_ids={company.id},
        active_result={
            "status": "success",
            "result": {"tally_company_guid": GUID},
        },
        sync_raises=True,
    )

    await _drive_auto_sync(conn)  # must not raise

    assert conn.calls == ["get_active_tally_company", "sync_masters"]


@pytest.mark.asyncio
async def test_schedule_respects_skip_dispatch_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # conftest default: TAXMIND_SKIP_TALLY_DISPATCH=1 -- scheduling must
    # be a no-op (no background task created, no command sent).
    from app.api.v1 import connector_ws as mod

    called = False

    async def _fail_if_called(conn: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mod, "_drive_auto_sync", _fail_if_called)
    _schedule_auto_sync_on_connector_up(conn=object())  # type: ignore[arg-type]

    import asyncio

    await asyncio.sleep(0)  # let any scheduled task (there should be none) run
    assert called is False
