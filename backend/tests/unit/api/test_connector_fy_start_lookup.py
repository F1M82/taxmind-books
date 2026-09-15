"""Unit tests for the live financial_year_start lookup used by
POST /connector/tally-mapping (see CLAUDE.md "Tally company mapping" and
the 2026-09-15 investigation that found Company.financial_year_start
silently defaulting to a stale hardcoded date for every company
connected via the discovery/mapping flow).

Exercised as a plain async function against a fake registry, rather
than a full WS round-trip, since the behavior under test is the
match/mismatch/failure logic -- not the WS transport itself (already
covered elsewhere).
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from app.api.v1 import connector as connector_api
from app.models.connector import TallyCompanyDiscovery


def _discovery(**overrides: object) -> TallyCompanyDiscovery:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "connector_id": uuid4(),
        "data_folder_path": "C:/Tally/Data",
        "tally_company_identifier": "100010",
        "tally_company_name": "Vighnaharta",
        "tally_master_id": "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9",
    }
    defaults.update(overrides)
    return TallyCompanyDiscovery(**defaults)


class _FakeRegistry:
    def __init__(self, *, online: bool, result: dict[str, object] | None = None, raises: Exception | None = None) -> None:
        self._online = online
        self._result = result
        self._raises = raises

    def is_online(self, *, connector_id: object) -> bool:
        return self._online

    async def send_command(self, **kwargs: object) -> dict[str, object]:
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


@pytest.mark.asyncio
async def test_returns_none_when_connector_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        connector_api._connector_registry_mod, "get_registry",
        lambda: _FakeRegistry(online=False),
    )
    result = await connector_api._live_financial_year_start_if_matching(_discovery(), uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_returns_fy_start_when_guid_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    discovery = _discovery()
    monkeypatch.setattr(
        connector_api._connector_registry_mod, "get_registry",
        lambda: _FakeRegistry(online=True, result={
            "status": "success",
            "result": {
                "tally_company_guid": discovery.tally_master_id,
                "active_company_identifier": discovery.tally_company_identifier,
                "financial_year_start": "2025-04-01",
            },
        }),
    )
    result = await connector_api._live_financial_year_start_if_matching(discovery, uuid4())
    assert result == date(2025, 4, 1)


@pytest.mark.asyncio
async def test_returns_none_when_active_company_is_a_different_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: must not stamp one company's FY-start onto a
    # different company just because SOME company happens to be open.
    discovery = _discovery()
    monkeypatch.setattr(
        connector_api._connector_registry_mod, "get_registry",
        lambda: _FakeRegistry(online=True, result={
            "status": "success",
            "result": {
                "tally_company_guid": "some-other-guid",
                "active_company_identifier": "999999",
                "financial_year_start": "2025-04-01",
            },
        }),
    )
    result = await connector_api._live_financial_year_start_if_matching(discovery, uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.tally.connector_registry import CommandTimeout

    monkeypatch.setattr(
        connector_api._connector_registry_mod, "get_registry",
        lambda: _FakeRegistry(online=True, raises=CommandTimeout("no reply")),
    )
    result = await connector_api._live_financial_year_start_if_matching(_discovery(), uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_tally_reports_no_fy_start(monkeypatch: pytest.MonkeyPatch) -> None:
    discovery = _discovery()
    monkeypatch.setattr(
        connector_api._connector_registry_mod, "get_registry",
        lambda: _FakeRegistry(online=True, result={
            "status": "success",
            "result": {
                "tally_company_guid": discovery.tally_master_id,
                "active_company_identifier": discovery.tally_company_identifier,
                "financial_year_start": None,
            },
        }),
    )
    result = await connector_api._live_financial_year_start_if_matching(discovery, uuid4())
    assert result is None
