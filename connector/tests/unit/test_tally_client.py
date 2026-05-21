"""Unit tests for tally_client.py — pytest-httpx fakes against canned XML."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pytest_httpx import HTTPXMock

from connector.tally_client import (
    ImportResponse,
    LedgerEntryInput,
    TallyAmbiguousResponse,
    TallyClient,
    TallyImportRejected,
    TallyParseError,
    TallyResponseError,
    TallyUnreachable,
    VoucherInput,
    _decimal,
    _fiscal_year_end,
    _fiscal_year_start,
    _parse_import_response,
    _parse_tally_date,
)

# ---------------- ImportData response fixtures ----------------
#
# Layer A (BUG-Books-004): connector must distinguish success / rejection /
# ambiguous response shapes for ImportData operations (post_voucher,
# approve_optional_voucher, reject_optional_voucher).
#
# _IMPORT_REJECTION_LEDGER_MISSING is live-captured 2026-05-19 12:39 UTC
# via direct httpx probe against TallyPrime with no company loaded.
# Documented in bug_books_004 memory file. Real Tally response, byte-exact.
#
# _IMPORT_SUCCESS_CREATE / _ALTER / _DELETE are reasoned from TDL docs
# (Tally Developer's Reference, ImportData section). Marked as
# documentation-based-not-empirically-verified — §7.5b validation pass
# must capture real success envelopes and replace if shapes differ.

_IMPORT_REJECTION_LEDGER_MISSING = (
    "<RESPONSE>\n"
    " <LINEERROR>Ledger &apos;Sales&apos; does not exist!</LINEERROR>\n"
    " <CREATED>0</CREATED>\n"
    " <ALTERED>0</ALTERED>\n"
    " <DELETED>0</DELETED>\n"
    " <LASTVCHID>0</LASTVCHID>\n"
    " <LASTMID>0</LASTMID>\n"
    " <COMBINED>0</COMBINED>\n"
    " <IGNORED>0</IGNORED>\n"
    " <ERRORS>0</ERRORS>\n"
    " <CANCELLED>0</CANCELLED>\n"
    " <EXCEPTIONS>1</EXCEPTIONS>\n"
    "</RESPONSE>"
)

# Documentation-based; replace with real capture during §7.5b validation.
_IMPORT_SUCCESS_CREATE = (
    "<RESPONSE>\n"
    " <CREATED>1</CREATED>\n"
    " <ALTERED>0</ALTERED>\n"
    " <DELETED>0</DELETED>\n"
    " <LASTVCHID>42</LASTVCHID>\n"
    " <LASTMID>0</LASTMID>\n"
    " <COMBINED>0</COMBINED>\n"
    " <IGNORED>0</IGNORED>\n"
    " <ERRORS>0</ERRORS>\n"
    " <CANCELLED>0</CANCELLED>\n"
    " <EXCEPTIONS>0</EXCEPTIONS>\n"
    "</RESPONSE>"
)

# Documentation-based; replace with real capture during §7.5b validation.
_IMPORT_SUCCESS_ALTER = (
    "<RESPONSE>\n"
    " <CREATED>0</CREATED>\n"
    " <ALTERED>1</ALTERED>\n"
    " <DELETED>0</DELETED>\n"
    " <LASTVCHID>42</LASTVCHID>\n"
    " <LASTMID>0</LASTMID>\n"
    " <COMBINED>0</COMBINED>\n"
    " <IGNORED>0</IGNORED>\n"
    " <ERRORS>0</ERRORS>\n"
    " <CANCELLED>0</CANCELLED>\n"
    " <EXCEPTIONS>0</EXCEPTIONS>\n"
    "</RESPONSE>"
)

# Documentation-based; replace with real capture during §7.5b validation.
_IMPORT_SUCCESS_DELETE = (
    "<RESPONSE>\n"
    " <CREATED>0</CREATED>\n"
    " <ALTERED>0</ALTERED>\n"
    " <DELETED>1</DELETED>\n"
    " <LASTVCHID>42</LASTVCHID>\n"
    " <LASTMID>0</LASTMID>\n"
    " <COMBINED>0</COMBINED>\n"
    " <IGNORED>0</IGNORED>\n"
    " <ERRORS>0</ERRORS>\n"
    " <CANCELLED>0</CANCELLED>\n"
    " <EXCEPTIONS>0</EXCEPTIONS>\n"
    "</RESPONSE>"
)

# Partial-success ambiguity: created AND exception both > 0.
_IMPORT_AMBIGUOUS_PARTIAL = (
    "<RESPONSE>\n"
    " <CREATED>1</CREATED>\n"
    " <ALTERED>0</ALTERED>\n"
    " <DELETED>0</DELETED>\n"
    " <LASTVCHID>42</LASTVCHID>\n"
    " <EXCEPTIONS>1</EXCEPTIONS>\n"
    "</RESPONSE>"
)

# Zero-everything ambiguity: Tally returned but nothing happened and
# no error was reported. Should also surface for investigation.
_IMPORT_AMBIGUOUS_ZERO = (
    "<RESPONSE>\n"
    " <CREATED>0</CREATED>\n"
    " <ALTERED>0</ALTERED>\n"
    " <DELETED>0</DELETED>\n"
    " <EXCEPTIONS>0</EXCEPTIONS>\n"
    "</RESPONSE>"
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


# Mirrors the real Tally TDL-Collection response shape: NAME is an XML
# attribute of <LEDGER>, PARENT is a child element with a TYPE attribute,
# PARTYGSTIN is the GSTIN field (not REGISTRATIONTYPE, which is the
# registration-type enum: Regular / Composition / Consumer / Unregistered).
_LEDGERS_XML = """<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="8">
        <LEDGER NAME="Sharma Traders" RESERVEDNAME="">
          <PARENT TYPE="String">Sundry Debtors</PARENT>
          <PARTYGSTIN>27BBBBB5678B1Z5</PARTYGSTIN>
        </LEDGER>
        <LEDGER NAME="Bank Account" RESERVEDNAME="">
          <PARENT TYPE="String">Bank Accounts</PARENT>
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


_GROUPS_XML = """<ENVELOPE>
  <BODY>
    <DATA>
      <COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="4">
        <GROUP NAME="Sundry Debtors" RESERVEDNAME="">
          <PARENT TYPE="String">Current Assets</PARENT>
        </GROUP>
        <GROUP NAME="Bank Accounts" RESERVEDNAME="Bank Accounts">
          <PARENT TYPE="String">Current Assets</PARENT>
        </GROUP>
      </COLLECTION>
    </DATA>
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
        return httpx.Response(200, text=_IMPORT_SUCCESS_CREATE)

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
        return httpx.Response(200, text=_IMPORT_SUCCESS_CREATE)

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
        return httpx.Response(200, text=_IMPORT_SUCCESS_CREATE)

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
        return httpx.Response(200, text=_IMPORT_SUCCESS_ALTER)

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
        return httpx.Response(200, text=_IMPORT_SUCCESS_DELETE)

    httpx_mock.add_callback(_capture, url="http://localhost:9000")
    result = await client.reject_optional_voucher("V-GUID-456")
    assert result["status"] == "success"
    assert result["tally_voucher_guid"] == "V-GUID-456"

    body = captured["body"]
    assert 'REMOTEID="V-GUID-456"' in body
    assert 'ACTION="Delete"' in body
    # Delete envelope must NOT carry any voucher payload.
    assert "<ALLLEDGERENTRIES.LIST>" not in body


# ---------------- _parse_import_response ----------------


def test_parse_import_response_rejection_envelope() -> None:
    parsed = _parse_import_response(_IMPORT_REJECTION_LEDGER_MISSING)
    assert parsed.created == 0
    assert parsed.altered == 0
    assert parsed.deleted == 0
    assert parsed.exceptions == 1
    assert parsed.line_error == "Ledger 'Sales' does not exist!"
    assert parsed.last_vch_id == "0"
    assert parsed.raw_body == _IMPORT_REJECTION_LEDGER_MISSING


def test_parse_import_response_success_create() -> None:
    parsed = _parse_import_response(_IMPORT_SUCCESS_CREATE)
    assert parsed.created == 1
    assert parsed.altered == 0
    assert parsed.deleted == 0
    assert parsed.exceptions == 0
    assert parsed.line_error is None
    assert parsed.last_vch_id == "42"


def test_parse_import_response_success_alter() -> None:
    parsed = _parse_import_response(_IMPORT_SUCCESS_ALTER)
    assert parsed.altered == 1
    assert parsed.created == 0
    assert parsed.exceptions == 0


def test_parse_import_response_success_delete() -> None:
    parsed = _parse_import_response(_IMPORT_SUCCESS_DELETE)
    assert parsed.deleted == 1
    assert parsed.exceptions == 0


def test_parse_import_response_missing_counters_default_zero() -> None:
    # Minimal envelope with no counters: parser defaults all to 0 so the
    # strict-shape predicate downstream routes through the ambiguous branch.
    parsed = _parse_import_response("<RESPONSE></RESPONSE>")
    assert parsed.created == 0
    assert parsed.altered == 0
    assert parsed.deleted == 0
    assert parsed.exceptions == 0
    assert parsed.line_error is None
    assert parsed.last_vch_id is None


def test_parse_import_response_malformed_xml_raises_parse_error() -> None:
    with pytest.raises(TallyParseError):
        _parse_import_response("<not valid")


# ---------------- post_voucher: rejection + ambiguous paths ----------------


@pytest.mark.asyncio
async def test_post_voucher_raises_TallyImportRejected_on_strict_rejection(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_REJECTION_LEDGER_MISSING,
    )
    with pytest.raises(TallyImportRejected) as exc_info:
        await client.post_voucher(_minimal_voucher())
    assert exc_info.value.line_error == "Ledger 'Sales' does not exist!"
    assert exc_info.value.exceptions == 1
    assert exc_info.value.raw_body == _IMPORT_REJECTION_LEDGER_MISSING


@pytest.mark.asyncio
async def test_post_voucher_raises_TallyAmbiguousResponse_on_partial(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # CREATED=1 + EXCEPTIONS=1 — neither strict success nor strict
    # rejection. Surface for investigation rather than bucket silently.
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_AMBIGUOUS_PARTIAL,
    )
    with pytest.raises(TallyAmbiguousResponse) as exc_info:
        await client.post_voucher(_minimal_voucher())
    assert exc_info.value.parsed.created == 1
    assert exc_info.value.parsed.exceptions == 1


@pytest.mark.asyncio
async def test_post_voucher_raises_TallyAmbiguousResponse_on_zero_everything(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # All counters zero, no exceptions, no line error. Tally said
    # nothing happened and nothing failed — unknown shape.
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_AMBIGUOUS_ZERO,
    )
    with pytest.raises(TallyAmbiguousResponse):
        await client.post_voucher(_minimal_voucher())


@pytest.mark.asyncio
async def test_post_voucher_returns_tally_voucher_guid_none(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # Layer C deferred: tally_voucher_guid is explicitly None on success
    # until REMOTEID survivability is probed in §7.5b validation.
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_SUCCESS_CREATE,
    )
    result = await client.post_voucher(_minimal_voucher())
    assert result["status"] == "success"
    assert result["tally_voucher_guid"] is None


# ---------------- approve/reject: rejection + success counters ----------------


@pytest.mark.asyncio
async def test_approve_optional_voucher_raises_on_altered_zero(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # CREATED=0, ALTERED=0, EXCEPTIONS=1, LINEERROR present →
    # strict rejection (the expected counter for Alter is ALTERED).
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_REJECTION_LEDGER_MISSING,
    )
    with pytest.raises(TallyImportRejected):
        await client.approve_optional_voucher("V-GUID-unknown")


@pytest.mark.asyncio
async def test_approve_optional_voucher_raises_ambiguous_on_create_only(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # Tally returned a "create succeeded" envelope to an Alter request.
    # ALTERED=0 (the counter the helper checks for op=alter) + zero
    # exceptions = ambiguous. Cross-op shape mismatch surfaces.
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_SUCCESS_CREATE,
    )
    with pytest.raises(TallyAmbiguousResponse):
        await client.approve_optional_voucher("V-GUID-123")


@pytest.mark.asyncio
async def test_reject_optional_voucher_raises_on_deleted_zero(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_IMPORT_REJECTION_LEDGER_MISSING,
    )
    with pytest.raises(TallyImportRejected):
        await client.reject_optional_voucher("V-GUID-unknown")


# ---------------- ImportResponse dataclass sanity ----------------


def test_import_response_dataclass_is_frozen() -> None:
    r = ImportResponse(
        created=1,
        altered=0,
        deleted=0,
        exceptions=0,
        last_vch_id="42",
        line_error=None,
        raw_body="<RESPONSE/>",
    )
    with pytest.raises(FrozenInstanceError):
        r.created = 2  # type: ignore[misc]
