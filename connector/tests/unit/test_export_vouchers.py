"""P3.1 — unit + golden-fixture tests for the read-only voucher export.

Covers: request building (read-only by construction), single-block parsing
(golden real fixture + synthetic edge cases), streaming block extraction
across arbitrary chunk boundaries, `get_vouchers` over a mocked transport,
date-window/pagination, malformed-input safety, and the WS command's
read-only classification + reconnect (re-run) safety.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from connector import message_handlers as mh
from connector.message_handlers import MUTATING_COMMANDS, dispatch_command
from connector.tally_client import (
    LedgerMaster,
    TallyClient,
    TallyParseError,
    TallyResponseError,
    TallyUnreachable,
    VoucherExportRow,
    _parse_voucher_block,
    _VoucherBlockScanner,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GOLDEN = (FIXTURES / "voucher_real_receipt.xml").read_text(encoding="utf-8")

# --- synthetic fixtures (modelled on the real Tally schema) ------------
# Clearly synthetic: the test company only holds simple 2-line Receipts, so
# multi-ledger / optional / cancelled / blank-number cases are authored here
# from Tally's documented voucher shape + the real skeleton observed in P3.0.

SYN_MULTILEDGER = (
    '<VOUCHER REMOTEID="G-J" VCHKEY="K-J" VCHTYPE="Journal">'
    "<DATE>20240515</DATE><GUID>G-J</GUID>"
    "<NARRATION>Synthetic journal</NARRATION>"
    "<VOUCHERTYPENAME>Journal</VOUCHERTYPENAME><VOUCHERNUMBER>J-7</VOUCHERNUMBER>"
    "<MASTERID> 21</MASTERID><ALTERID> 3</ALTERID>"
    "<ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><ISDELETED>No</ISDELETED>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Rent A/c</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>500.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Salary A/c</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>300.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Payable</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>-800.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "</VOUCHER>"
)

SYN_OPTIONAL = (
    '<VOUCHER REMOTEID="G-O" VCHTYPE="Sales">'
    "<DATE>20250101</DATE><GUID>G-O</GUID><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>"
    "<VOUCHERNUMBER>S-1</VOUCHERNUMBER>"
    "<ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>Yes</ISOPTIONAL><ISDELETED>No</ISDELETED>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Debtor</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>118.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>-118.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "</VOUCHER>"
)

SYN_CANCELLED = (
    '<VOUCHER REMOTEID="G-C" VCHTYPE="Payment">'
    "<DATE>20250202</DATE><GUID>G-C</GUID><VOUCHERTYPENAME>Payment</VOUCHERTYPENAME>"
    "<VOUCHERNUMBER>P-9</VOUCHERNUMBER>"
    "<ISCANCELLED>Yes</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><ISDELETED>No</ISDELETED>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>0.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "</VOUCHER>"
)

SYN_BLANK_NUMBER = (
    '<VOUCHER REMOTEID="G-B" VCHTYPE="Contra">'
    "<DATE>20250303</DATE><GUID>G-B</GUID><VOUCHERTYPENAME>Contra</VOUCHERTYPENAME>"
    "<VOUCHERNUMBER></VOUCHERNUMBER>"
    "<ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><ISDELETED>No</ISDELETED>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>50.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Bank</LEDGERNAME>"
    "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>-50.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
    "</VOUCHER>"
)


def _collection(*voucher_blocks: str) -> str:
    """Wrap voucher blocks in a realistic export envelope, including the
    CMPINFO ``<VOUCHER>N</VOUCHER>`` counter the scanner must skip."""
    n = len(voucher_blocks)
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>"
        f"<BODY><DESC><CMPINFO><VOUCHER>{n}</VOUCHER></CMPINFO></DESC>"
        "<DATA><COLLECTION>" + "".join(voucher_blocks) + "</COLLECTION></DATA>"
        "</BODY></ENVELOPE>"
    )


# Ledger-master list the export command fetches to enrich each voucher
# line with its ledger GUID (P3.7 Phase 6A). Mirrors the probe-captured
# TDL-Collection response with <GUID> populated.
_LEDGERS_XML_WITH_GUID = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    '<LEDGER NAME="HDFC BANK" RESERVEDNAME="">'
    '<GUID TYPE="String">ed86199b-f679-4450-9a9e-70673d09c6f8-000000cd</GUID>'
    "<PARENT TYPE=\"String\">Bank Accounts</PARENT></LEDGER>"
    '<LEDGER NAME="Xyz Ltd" RESERVEDNAME="">'
    '<GUID TYPE="String">ed86199b-f679-4450-9a9e-70673d09c6f8-0000001f</GUID>'
    "<PARENT TYPE=\"String\">Sundry Debtors</PARENT></LEDGER>"
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)


@pytest.fixture
def client() -> TallyClient:
    return TallyClient(host="localhost", port=9000, timeout=5.0)


# ====================================================================
# 1. Request building is READ-ONLY by construction
# ====================================================================


def test_build_get_vouchers_xml_is_read_only() -> None:
    xml = TallyClient._build_get_vouchers_xml("20260401", "20270331")
    # Export request, not import.
    assert "<TALLYREQUEST>Export Data</TALLYREQUEST>" in xml
    assert "<TYPE>Collection</TYPE>" in xml
    # No write surface whatsoever.
    for forbidden in (
        "IMPORTDATA",
        "Import Data",
        "TALLYMESSAGE",
        'ACTION="Create"',
        'ACTION="Alter"',
        'ACTION="Delete"',
        "ACTION=",
    ):
        assert forbidden not in xml, f"read-only violation: {forbidden!r}"


def test_build_get_vouchers_xml_embeds_dates() -> None:
    xml = TallyClient._build_get_vouchers_xml("20230401", "20240331")
    assert '$$Date:"20230401"' in xml
    assert '$$Date:"20240331"' in xml
    assert "<FILTER>TaxMindDateFilter</FILTER>" in xml
    # Different windows produce different requests (pagination primitive).
    other = TallyClient._build_get_vouchers_xml("20240401", "20250331")
    assert other != xml
    assert '$$Date:"20240401"' in other


# ====================================================================
# 2. Single-block parsing — golden real fixture + synthetic edges
# ====================================================================


def test_parse_golden_real_receipt() -> None:
    row = _parse_voucher_block(GOLDEN)
    assert isinstance(row, VoucherExportRow)
    assert row.tally_guid == "ed86199b-f679-4450-9a9e-70673d09c6f8-00000001"
    assert row.remote_id == "ed86199b-f679-4450-9a9e-70673d09c6f8-00000001"
    assert row.vchkey and row.vchkey.startswith("ed86199b-")
    assert row.master_id == "1"  # leading Tally space stripped
    assert row.alter_id == "1"
    assert row.voucher_type == "Receipt"
    assert row.date == date(2026, 7, 21)
    assert row.voucher_number == "1"
    assert row.narration == "Live 7.5b happy-path validation 2026-07-21"
    assert row.is_cancelled is False
    assert row.is_optional is False
    assert row.is_deleted is False
    assert len(row.entries) == 2
    dr = [e for e in row.entries if e.entry_type == "Dr"]
    cr = [e for e in row.entries if e.entry_type == "Cr"]
    assert len(dr) == 1 and len(cr) == 1
    assert dr[0].ledger_name == "HDFC BANK" and dr[0].amount == Decimal("100.00")
    assert cr[0].ledger_name == "Xyz Ltd" and cr[0].amount == Decimal("-100.00")


def test_parse_multiledger() -> None:
    row = _parse_voucher_block(SYN_MULTILEDGER)
    assert row.voucher_type == "Journal"
    assert len(row.entries) == 3
    assert sum(e.amount for e in row.entries) == Decimal("0.00")
    assert [e.entry_type for e in row.entries] == ["Dr", "Dr", "Cr"]


def test_parse_optional_flag() -> None:
    row = _parse_voucher_block(SYN_OPTIONAL)
    assert row.is_optional is True
    assert row.is_cancelled is False


def test_parse_cancelled_flag() -> None:
    row = _parse_voucher_block(SYN_CANCELLED)
    assert row.is_cancelled is True


def test_parse_blank_voucher_number() -> None:
    row = _parse_voucher_block(SYN_BLANK_NUMBER)
    assert row.voucher_number is None  # empty element → None, not ""


def test_parse_malformed_raises_tally_parse_error() -> None:
    with pytest.raises(TallyParseError):
        _parse_voucher_block("<VOUCHER><DATE>2026 not closed")

def test_parse_voucher_with_udf_namespace() -> None:
    """Regression: real Tally exports embed ``UDF:``-namespaced user-defined
    fields whose xmlns lives on an ancestor, not on the <VOUCHER> block. Parsing
    an extracted block standalone must not fail with 'unbound prefix'. (Minimal
    SYNTHETIC repro of a structure observed in real export data — no real
    financial values.)"""
    block = (
        '<VOUCHER REMOTEID="G-U" VCHTYPE="Debit Note">'
        "<DATE>20260601</DATE><GUID>G-U</GUID>"
        "<VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME><VOUCHERNUMBER>DN/4</VOUCHERNUMBER>"
        "<ISCANCELLED>No</ISCANCELLED><ISOPTIONAL>No</ISOPTIONAL><ISDELETED>No</ISDELETED>"
        "<COSTTRACKALLOCATIONS.LIST></COSTTRACKALLOCATIONS.LIST>"
        '<UDF:USERDESCRIPTION.LIST DESC="`User Description`" ISLIST="YES" TYPE="String" INDEX="29">'
        '<UDF:USERDESCRIPTION DESC="`User Description`">note</UDF:USERDESCRIPTION>'
        "</UDF:USERDESCRIPTION.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Creditor</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>90.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
        "<ALLLEDGERENTRIES.LIST><LEDGERNAME>Purchase Return</LEDGERNAME>"
        "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>-90.00</AMOUNT></ALLLEDGERENTRIES.LIST>"
        "</VOUCHER>"
    )
    row = _parse_voucher_block(block)
    assert row.voucher_type == "Debit Note"
    assert row.voucher_number == "DN/4"
    assert len(row.entries) == 2  # UDF element ignored; only ledger lines read
    assert {e.entry_type for e in row.entries} == {"Dr", "Cr"}


def test_golden_empty_placeholders_are_tolerated() -> None:
    """Real Tally vouchers carry empty BILLALLOCATIONS / cost-centre / TDS
    placeholder tags and an empty REFERENCE element (observed in P3.0 on the
    live company). The parser must ignore them cleanly — no phantom ledger
    entries, no crash — rather than treat them as data."""
    assert "<BILLALLOCATIONS.LIST>" in GOLDEN  # the real skeleton is present…
    assert "TDSDEDUCTEE" in GOLDEN
    row = _parse_voucher_block(GOLDEN)
    # …but only the two genuine Dr/Cr ledger lines are extracted.
    assert len(row.entries) == 2
    assert {e.ledger_name for e in row.entries} == {"HDFC BANK", "Xyz Ltd"}
    # empty <REFERENCE></REFERENCE> → None, not "".
    assert row.reference is None


# ====================================================================
# 2b. Ledger-GUID enrichment (P3.7 Phase 6A)
# ====================================================================


def test_enrich_ledger_guids_exact_name_match() -> None:
    row = _parse_voucher_block(GOLDEN)
    ledgers = [
        LedgerMaster(name="HDFC BANK", parent_group="Bank Accounts",
                     master_id="guid-bank"),
        LedgerMaster(name="Xyz Ltd", parent_group="Sundry Debtors",
                     master_id="guid-xyz"),
    ]
    [enriched] = TallyClient.enrich_ledger_guids([row], ledgers)
    by_name = {e.ledger_name: e.ledger_guid for e in enriched.entries}
    assert by_name["HDFC BANK"] == "guid-bank"
    assert by_name["Xyz Ltd"] == "guid-xyz"


def test_enrich_ledger_guids_ambiguous_name_left_none() -> None:
    row = _parse_voucher_block(GOLDEN)
    # Two ledgers share the normalized name → ambiguous, no GUID attached.
    ledgers = [
        LedgerMaster(name="hdfc bank", parent_group="Bank Accounts",
                     master_id="guid-a"),
        LedgerMaster(name="HDFC BANK", parent_group="Bank Accounts",
                     master_id="guid-b"),
        LedgerMaster(name="Xyz Ltd", parent_group="Sundry Debtors",
                     master_id="guid-xyz"),
    ]
    [enriched] = TallyClient.enrich_ledger_guids([row], ledgers)
    by_name = {e.ledger_name: e.ledger_guid for e in enriched.entries}
    assert by_name["HDFC BANK"] is None  # ambiguous — must not guess
    assert by_name["Xyz Ltd"] == "guid-xyz"


def test_enrich_ledger_guids_missing_ledger_left_none() -> None:
    row = _parse_voucher_block(GOLDEN)
    [enriched] = TallyClient.enrich_ledger_guids([row], [])
    assert all(e.ledger_guid is None for e in enriched.entries)


# ====================================================================
# 3. Streaming block extraction across arbitrary chunk boundaries
# ====================================================================


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 50, 100000])
def test_scanner_is_chunk_boundary_agnostic(chunk_size: int) -> None:
    doc = _collection(GOLDEN, SYN_MULTILEDGER, SYN_OPTIONAL)
    scanner = _VoucherBlockScanner()
    blocks: list[str] = []
    for i in range(0, len(doc), chunk_size):
        blocks.extend(scanner.feed(doc[i : i + chunk_size]))
    # Exactly the 3 REAL vouchers; the CMPINFO <VOUCHER>3</VOUCHER> is skipped.
    assert len(blocks) == 3
    rows = [_parse_voucher_block(b) for b in blocks]
    assert [r.voucher_type for r in rows] == ["Receipt", "Journal", "Sales"]


def test_scanner_skips_cmpinfo_counter() -> None:
    scanner = _VoucherBlockScanner()
    blocks = scanner.feed(_collection(GOLDEN))
    assert len(blocks) == 1  # the <VOUCHER>1</VOUCHER> counter is not a voucher


def test_scanner_holds_incomplete_block() -> None:
    scanner = _VoucherBlockScanner()
    # An open voucher with no close yet → nothing emitted, no crash.
    assert scanner.feed('<VOUCHER VCHTYPE="Receipt"><DATE>20260101</DATE>') == []
    # Completing it later yields exactly one.
    assert len(scanner.feed("<GUID>x</GUID></VOUCHER>")) == 1


# ====================================================================
# 4. get_vouchers over a mocked transport (+ pagination, errors)
# ====================================================================


@pytest.mark.asyncio
async def test_get_vouchers_returns_rows(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_collection(GOLDEN, SYN_MULTILEDGER),
    )
    rows = await client.get_vouchers("20000401", "20300331")
    assert [r.voucher_type for r in rows] == ["Receipt", "Journal"]
    assert rows[0].tally_guid == "ed86199b-f679-4450-9a9e-70673d09c6f8-00000001"


@pytest.mark.asyncio
async def test_get_vouchers_date_window_is_sent(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=_collection(GOLDEN)
    )
    await client.get_vouchers("20240401", "20250331")
    sent = httpx_mock.get_requests()[0].content.decode("utf-8")
    assert '$$Date:"20240401"' in sent and '$$Date:"20250331"' in sent
    assert "Export Data" in sent and "IMPORTDATA" not in sent


@pytest.mark.asyncio
async def test_get_vouchers_empty_window(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=_collection()
    )
    assert await client.get_vouchers("20200401", "20200430") == []


@pytest.mark.asyncio
async def test_get_vouchers_non_200_raises(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="http://localhost:9000", status_code=500, text="boom")
    with pytest.raises(TallyResponseError) as ei:
        await client.get_vouchers("20260401", "20270331")
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_get_vouchers_connect_error_raises_unreachable(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(TallyUnreachable):
        await client.get_vouchers("20260401", "20270331")


# ====================================================================
# 5. WS command: read-only classification + dispatch + reconnect safety
# ====================================================================


def test_export_vouchers_is_not_a_mutating_command() -> None:
    # The read-only guarantee at the dispatch layer: it bypasses the
    # idempotency cache and can never be treated as a write.
    assert "export_vouchers" not in MUTATING_COMMANDS
    assert "export_vouchers" in mh.HANDLERS


@pytest.mark.asyncio
async def test_dispatch_export_vouchers_success(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_collection(GOLDEN, SYN_OPTIONAL),
    )
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_LEDGERS_XML_WITH_GUID,
    )
    reply = await dispatch_command(
        tally=client,
        payload={
            "command": "export_vouchers",
            "company_id": "c-1",
            "args": {"from_date": "20260401", "to_date": "20270331"},
        },
        registered_company_id="c-1",
    )
    assert reply["status"] == "success"
    result = reply["result"]
    assert result["count"] == 2
    assert result["from_date"] == "20260401"
    assert result["vouchers"][0]["voucher_type"] == "Receipt"
    assert result["vouchers"][1]["is_optional"] is True


@pytest.mark.asyncio
async def test_dispatch_export_vouchers_rejects_bad_dates(
    client: TallyClient,
) -> None:
    with pytest.raises(ValueError, match="from_date"):
        await dispatch_command(
            tally=client,
            payload={
                "command": "export_vouchers",
                "company_id": "c-1",
                "args": {"from_date": "2026-04-01", "to_date": "20270331"},
            },
            registered_company_id="c-1",
        )


@pytest.mark.asyncio
async def test_reconnect_rerun_is_side_effect_free(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # Simulate a reconnect: the same window is exported twice. Because the
    # command is read-only (no cache, no Tally write), both runs return the
    # same fresh data with no accumulated/corrupted state.
    for _ in range(2):
        httpx_mock.add_response(
            url="http://localhost:9000",
            status_code=200,
            text=_collection(GOLDEN, SYN_MULTILEDGER),
        )
        httpx_mock.add_response(
            url="http://localhost:9000",
            status_code=200,
            text=_LEDGERS_XML_WITH_GUID,
        )
    payload = {
        "command": "export_vouchers",
        "company_id": "c-1",
        "args": {"from_date": "20260401", "to_date": "20270331"},
    }
    first = await dispatch_command(
        tally=client, payload=payload, registered_company_id="c-1"
    )
    second = await dispatch_command(
        tally=client, payload=payload, registered_company_id="c-1"
    )
    assert first["result"] == second["result"]
    assert first["result"]["count"] == 2
