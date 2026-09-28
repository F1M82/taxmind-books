"""get_stock_valuation: Tally's own opening/closing stock for the gateway's
active period (read-only; backend Dr-positive sign)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

from connector.message_handlers import MUTATING_COMMANDS, dispatch_command
from connector.tally_client import (
    CompanyInfo,
    StockValuation,
    TallyClient,
    TallyParseError,
)


@pytest.fixture
def client() -> TallyClient:
    return TallyClient(host="localhost", port=9000, timeout=5.0)


def _period(frm: str = "20250401", to: str = "20260331") -> str:
    return (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        f'<COMPANY NAME="X"><TMFROM>{frm}</TMFROM><TMTO>{to}</TMTO></COMPANY>'
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )


def _item(name: str, ov: str, cv: str, cq: str = "") -> str:
    return (
        f'<STOCKITEM NAME="{name}">'
        f"<OPENINGVALUE>{ov}</OPENINGVALUE>"
        f"<CLOSINGVALUE>{cv}</CLOSINGVALUE>"
        f"<CLOSINGBALANCE>{cq}</CLOSINGBALANCE></STOCKITEM>"
    )


def _items(*items: str) -> str:
    return (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        + "".join(items)
        + "</COLLECTION></DATA></BODY></ENVELOPE>"
    )


def _stub(httpx_mock: HTTPXMock, body: str, period: str | None = None) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=period or _period()
    )
    httpx_mock.add_response(url="http://localhost:9000", status_code=200, text=body)


@pytest.mark.asyncio
async def test_flips_tally_sign_to_backend_dr_positive(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # Tally: debit stock is NEGATIVE. Opening Dr 1,000 -> +1000 backend.
    # Closing +600 in Tally is a CREDIT (negative stock) -> -600 backend.
    _stub(
        httpx_mock,
        _items(
            _item("A", "-1000.00", "-400.00", "10 NOS"),
            _item("B", "0", "1000.00", "-206 NOS"),
        ),
    )
    v = await client.get_stock_valuation()
    assert v.opening_value == Decimal("1000.00")
    assert v.closing_value == Decimal("-600.00")


@pytest.mark.asyncio
async def test_reports_period_and_item_counts(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    _stub(
        httpx_mock,
        _items(
            _item("A", "-1000.00", "-400.00", "10 NOS"),
            _item("B", "0", "1000.00", "-206 NOS"),
            _item("C", "", "", ""),
            _item("D", "0", "0", "-5 NOS"),
        ),
        period=_period("20240401", "20250331"),
    )
    v = await client.get_stock_valuation()
    assert (v.period_from, v.period_to) == (date(2024, 4, 1), date(2025, 3, 31))
    assert v.item_count == 4
    assert v.items_with_value == 2  # A and B; C empty, D zero value
    assert v.negative_stock_items == 2  # B and D have negative quantity


@pytest.mark.asyncio
async def test_no_stock_items_is_zero_not_an_error(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    _stub(httpx_mock, _items())
    v = await client.get_stock_valuation()
    assert v.opening_value == 0 and v.closing_value == 0 and v.item_count == 0


@pytest.mark.asyncio
async def test_tally_rejection_raises(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    _stub(httpx_mock, "<RESPONSE>Unknown Request</RESPONSE>")
    with pytest.raises(TallyParseError):
        await client.get_stock_valuation()


@pytest.mark.asyncio
async def test_request_is_read_only_and_sends_no_date_static_variables(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    _stub(httpx_mock, _items())
    await client.get_stock_valuation()
    sent = httpx_mock.get_requests()[1].content.decode("utf-8")  # [0] = period
    assert "<SVFROMDATE>" not in sent and "<SVTODATE>" not in sent
    assert "IMPORTDATA" not in sent and "ACTION" not in sent
    assert "<TYPE>StockItem</TYPE>" in sent


def test_command_is_registered_and_not_mutating() -> None:
    from connector.message_handlers import HANDLERS

    assert "get_stock_valuation" in HANDLERS
    assert "get_stock_valuation" not in MUTATING_COMMANDS


@pytest.mark.asyncio
async def test_dispatch_returns_identity_period_and_values() -> None:
    c = TallyClient(host="x", port=9000)
    c.get_company_info = AsyncMock(  # type: ignore[method-assign]
        return_value=CompanyInfo(
            name="ACME",
            guid="c30a0ee5-0000-0000-0000-000000000000",
            financial_year_start=date(2025, 4, 1),
        )
    )
    c.get_stock_valuation = AsyncMock(  # type: ignore[method-assign]
        return_value=StockValuation(
            period_from=date(2025, 4, 1),
            period_to=date(2026, 3, 31),
            opening_value=Decimal("733801.87"),
            closing_value=Decimal("-139818.21"),
            item_count=210,
            items_with_value=39,
            negative_stock_items=18,
        )
    )
    reply = await dispatch_command(
        tally=c,
        payload={"command": "get_stock_valuation", "args": {}, "company_id": "C"},
        registered_company_id="C",
    )
    assert reply["status"] == "success", reply
    r = reply["result"]
    assert r["company"] == {
        "name": "ACME",
        "guid": "c30a0ee5-0000-0000-0000-000000000000",
    }
    assert (r["period_from"], r["period_to"]) == ("2025-04-01", "2026-03-31")
    assert r["opening_value"] == "733801.87"
    assert r["closing_value"] == "-139818.21"
    assert r["negative_stock_items"] == 18
