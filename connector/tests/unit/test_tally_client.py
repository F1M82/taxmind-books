"""Unit tests for tally_client.py — pytest-httpx fakes against canned XML."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from pytest_httpx import HTTPXMock

from connector.tally_client import (
    LedgerEntryInput,
    TallyClient,
    TallyParseError,
    TallyResponseError,
    TallyUnreachable,
    VoucherInput,
    _decimal,
    _fiscal_year_end,
    _fiscal_year_start,
    _parse_tally_date,
)


@pytest.fixture
def client() -> TallyClient:
    return TallyClient(host="localhost", port=9000, timeout=5.0)


# ---------------- helpers ----------------


def test_fiscal_year_start_after_april() -> None:
    # 1 May 2026 → FY 2026-04-01 → 2027-03-31
    assert _fiscal_year_start(date(2026, 5, 1)) == "2026-04-01"
    assert _fiscal_year_end(date(2026, 5, 1)) == "2027-03-31"


def test_fiscal_year_start_before_april() -> None:
    # 15 Feb 2026 → FY 2025-04-01 → 2026-03-31
    assert _fiscal_year_start(date(2026, 2, 15)) == "2025-04-01"
    assert _fiscal_year_end(date(2026, 2, 15)) == "2026-03-31"


def test_parse_tally_date_valid() -> None:
    assert _parse_tally_date("20260508") == date(2026, 5, 8)


def test_parse_tally_date_invalid_falls_back_to_today() -> None:
    assert _parse_tally_date("garbage") == date.today()


def test_decimal_handles_blank_and_garbage() -> None:
    assert _decimal(None) == Decimal("0.00")
    assert _decimal("") == Decimal("0.00")
    assert _decimal("garbage") == Decimal("0.00")
    assert _decimal("1500.50") == Decimal("1500.50")


# ---------------- ping ----------------


@pytest.mark.asyncio
async def test_ping_returns_true_on_200(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="http://localhost:9000", status_code=200)
    assert await client.ping() is True


@pytest.mark.asyncio
async def test_ping_returns_false_on_500(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="http://localhost:9000", status_code=500)
    assert await client.ping() is False


@pytest.mark.asyncio
async def test_ping_returns_false_on_connection_error(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("nope"))
    assert await client.ping() is False


# ---------------- error handling ----------------


@pytest.mark.asyncio
async def test_get_all_groups_raises_on_500(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=500,
        text="boom",
    )
    with pytest.raises(TallyResponseError) as exc_info:
        await client.get_all_groups()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_get_all_groups_raises_unreachable_on_connect_error(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(TallyUnreachable):
        await client.get_all_groups()


@pytest.mark.asyncio
async def test_parse_error_raises_tally_parse_error(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text="<not valid xml",
    )
    with pytest.raises(TallyParseError):
        await client.get_all_groups()


# ---------------- get_all_ledgers ----------------


_LEDGERS_XML = """<?xml version="1.0" ?>
<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION>
        <LEDGER>
          <NAME>Sharma Traders</NAME>
          <PARENT>Sundry Debtors</PARENT>
          <REGISTRATIONTYPE>27BBBBB5678B1Z5</REGISTRATIONTYPE>
        </LEDGER>
        <LEDGER>
          <NAME>Bank Account</NAME>
          <PARENT>Bank Accounts</PARENT>
        </LEDGER>
      </COLLECTION>
    </DATA>
  </BODY>
</ENVELOPE>
"""


@pytest.mark.asyncio
async def test_get_all_ledgers_parses(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_LEDGERS_XML,
    )
    ledgers = await client.get_all_ledgers()
    assert len(ledgers) == 2
    by_name = {led.name: led for led in ledgers}
    assert by_name["Sharma Traders"].parent_group == "Sundry Debtors"
    assert by_name["Sharma Traders"].gstin == "27BBBBB5678B1Z5"
    assert by_name["Bank Account"].parent_group == "Bank Accounts"
    assert by_name["Bank Account"].gstin is None


# ---------------- get_all_groups ----------------


_GROUPS_XML = """<?xml version="1.0" ?>
<ENVELOPE>
  <BODY>
    <COLLECTION>
      <GROUP>
        <NAME>Sundry Debtors</NAME>
        <PARENT>Current Assets</PARENT>
      </GROUP>
      <GROUP>
        <NAME>Bank Accounts</NAME>
        <PARENT>Current Assets</PARENT>
      </GROUP>
    </COLLECTION>
  </BODY>
</ENVELOPE>
"""


@pytest.mark.asyncio
async def test_get_all_groups_parses(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_GROUPS_XML,
    )
    groups = await client.get_all_groups()
    assert {g.name for g in groups} == {"Sundry Debtors", "Bank Accounts"}
    assert all(g.parent == "Current Assets" for g in groups)


# ---------------- get_trial_balance ----------------


_TB_XML = """<?xml version="1.0" ?>
<ENVELOPE>
  <BODY>
    <DATA>
      <LEDGER>
        <NAME>Cash</NAME>
        <CLOSINGBALANCE>50000.00</CLOSINGBALANCE>
      </LEDGER>
      <LEDGER>
        <NAME>Sales</NAME>
        <CLOSINGBALANCE>-100000.00</CLOSINGBALANCE>
      </LEDGER>
    </DATA>
  </BODY>
</ENVELOPE>
"""


@pytest.mark.asyncio
async def test_get_trial_balance_uses_decimal(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=_TB_XML
    )
    rows = await client.get_trial_balance()
    by_name = {r.name: r for r in rows}
    assert by_name["Cash"].closing_balance == Decimal("50000.00")
    assert by_name["Sales"].closing_balance == Decimal("-100000.00")
    # Verify Decimal not float.
    assert isinstance(by_name["Cash"].closing_balance, Decimal)


# ---------------- get_outstanding ----------------


_OUT_XML = """<?xml version="1.0" ?>
<ENVELOPE>
  <BODY>
    <DATA>
      <LEDGER>
        <BILLALLOCATIONS.LIST>
          <NAME>INV-001</NAME>
          <AMOUNT>-15000.00</AMOUNT>
          <BILLDATE>20260415</BILLDATE>
        </BILLALLOCATIONS.LIST>
        <BILLALLOCATIONS.LIST>
          <NAME>INV-002</NAME>
          <AMOUNT>5000.00</AMOUNT>
        </BILLALLOCATIONS.LIST>
      </LEDGER>
    </DATA>
  </BODY>
</ENVELOPE>
"""


@pytest.mark.asyncio
async def test_get_outstanding_returns_absolute_amounts(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=_OUT_XML
    )
    items = await client.get_outstanding()
    by_name = {i.bill_name: i for i in items}
    assert by_name["INV-001"].amount == Decimal("15000.00")
    assert by_name["INV-002"].amount == Decimal("5000.00")
    assert by_name["INV-001"].due_date == "20260415"


# ---------------- get_ledger ----------------


_LEDGER_VOUCHERS_XML = """<?xml version="1.0" ?>
<ENVELOPE>
  <BODY>
    <DATA>
      <VOUCHER REMOTEID="abc-1" VCHTYPE="Receipt">
        <VOUCHERNUMBER>R-1</VOUCHERNUMBER>
        <DATE>20260508</DATE>
        <AMOUNT>5000.00</AMOUNT>
        <NARRATION>First payment</NARRATION>
      </VOUCHER>
      <VOUCHER REMOTEID="abc-2" VCHTYPE="Sales">
        <VOUCHERNUMBER>S-1</VOUCHERNUMBER>
        <DATE>20260510</DATE>
        <AMOUNT>-2000.00</AMOUNT>
        <NARRATION>Sales invoice</NARRATION>
      </VOUCHER>
    </DATA>
  </BODY>
</ENVELOPE>
"""


@pytest.mark.asyncio
async def test_get_ledger_aggregates_balances(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_LEDGER_VOUCHERS_XML,
    )
    result = await client.get_ledger("Sharma Traders")
    assert result["party_name"] == "Sharma Traders"
    assert result["transaction_count"] == 2
    # Net = 5000 - 2000 = 3000
    assert result["closing_balance"] == Decimal("3000.00")
    txns = result["transactions"]
    assert txns[0].voucher_number == "R-1"
    assert txns[0].voucher_date == date(2026, 5, 8)
    assert txns[0].amount == Decimal("5000.00")
    assert txns[1].amount == Decimal("-2000.00")


# ---------------- post_voucher ----------------


@pytest.mark.asyncio
async def test_post_voucher_builds_n_line_envelope(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    captured = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="<RESPONSE>OK</RESPONSE>")

    httpx_mock.add_callback(_capture, url="http://localhost:9000")

    voucher = VoucherInput(
        voucher_type="Receipt",
        voucher_date=date(2026, 5, 8),
        voucher_number="R-1",
        party_name="Sharma Traders",
        narration="Payment received",
        entries=[
            LedgerEntryInput(
                ledger_name="Bank",
                amount=Decimal("50000.00"),
                entry_type="Dr",
            ),
            LedgerEntryInput(
                ledger_name="Sharma Traders",
                amount=Decimal("50000.00"),
                entry_type="Cr",
            ),
        ],
    )
    result = await client.post_voucher(voucher)
    assert result["status"] == "success"
    assert result["voucher_number"] == "R-1"

    body = captured["body"]
    assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in body
    assert "<DATE>20260508</DATE>" in body
    assert '<VOUCHER VCHTYPE="Receipt" ACTION="Create">' in body
    # Two N-line entries with sign-correct amounts.
    assert body.count("<ALLLEDGERENTRIES.LIST>") == 2
    assert "<LEDGERNAME>Bank</LEDGERNAME>" in body
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in body
    assert "<LEDGERNAME>Sharma Traders</LEDGERNAME>" in body
    assert "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>" in body
    assert "<AMOUNT>50000.00</AMOUNT>" in body
    assert "<AMOUNT>-50000.00</AMOUNT>" in body
    # Default (as_optional=False) emits no ISOPTIONAL tag.
    assert "<ISOPTIONAL>" not in body


# ---------------- Optional voucher flow (v1.2) ----------------


def _minimal_voucher(*, as_optional: bool = False) -> VoucherInput:
    return VoucherInput(
        voucher_type="Sales",
        voucher_date=date(2026, 5, 8),
        voucher_number="S-1",
        party_name="Acme Co",
        narration="AI extracted",
        entries=[
            LedgerEntryInput(
                ledger_name="Acme Co",
                amount=Decimal("10000.00"),
                entry_type="Dr",
            ),
            LedgerEntryInput(
                ledger_name="Sales",
                amount=Decimal("10000.00"),
                entry_type="Cr",
            ),
        ],
        as_optional=as_optional,
    )


@pytest.mark.asyncio
async def test_post_voucher_as_optional_emits_isoptional_yes(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="<RESPONSE>OK</RESPONSE>")

    httpx_mock.add_callback(_capture, url="http://localhost:9000")
    result = await client.post_voucher(_minimal_voucher(as_optional=True))
    assert result["status"] == "success"
    assert result["as_optional"] is True
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in captured["body"]


@pytest.mark.asyncio
async def test_post_voucher_regular_omits_isoptional(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="<RESPONSE>OK</RESPONSE>")

    httpx_mock.add_callback(_capture, url="http://localhost:9000")
    result = await client.post_voucher(_minimal_voucher(as_optional=False))
    assert result["as_optional"] is False
    assert "<ISOPTIONAL>" not in captured["body"]


@pytest.mark.asyncio
async def test_approve_optional_voucher_alters_isoptional_to_no(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="<RESPONSE>OK</RESPONSE>")

    httpx_mock.add_callback(_capture, url="http://localhost:9000")
    result = await client.approve_optional_voucher("V-GUID-123")
    assert result["status"] == "success"
    assert result["tally_voucher_guid"] == "V-GUID-123"

    body = captured["body"]
    assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in body
    assert 'REMOTEID="V-GUID-123"' in body
    assert 'ACTION="Alter"' in body
    assert "<ISOPTIONAL>No</ISOPTIONAL>" in body


@pytest.mark.asyncio
async def test_reject_optional_voucher_deletes_via_remoteid(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, text="<RESPONSE>OK</RESPONSE>")

    httpx_mock.add_callback(_capture, url="http://localhost:9000")
    result = await client.reject_optional_voucher("V-GUID-456")
    assert result["status"] == "success"
    assert result["tally_voucher_guid"] == "V-GUID-456"

    body = captured["body"]
    assert 'REMOTEID="V-GUID-456"' in body
    assert 'ACTION="Delete"' in body
    # Delete envelope must NOT carry any voucher payload.
    assert "<ALLLEDGERENTRIES.LIST>" not in body
