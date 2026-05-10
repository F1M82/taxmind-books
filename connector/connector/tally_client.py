"""TallyPrime XML client.

Speaks XML over HTTP to TallyPrime's built-in server (default port
9000). All money values flow as Decimal; the salvaged version used
floats and silently lost precision on rupee/paise math — that's a
MONEY.md violation we fix here.

Configuration in TallyPrime:
    F12 (Configure) → Advanced Configuration → Configuration →
    ODBC → Enable Tally HTTP Server → Yes → Port: 9000

Per CONNECTOR_PROTOCOL.md command catalog, this client exposes:
    ping, get_ledger, get_all_ledgers, get_all_groups,
    post_voucher, get_trial_balance, get_outstanding

The v1.2 Optional-voucher extensions (`as_optional` flag,
approve_optional_voucher, reject_optional_voucher) land in P0.46.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------


class TallyError(Exception):
    """Base for any failure talking to TallyPrime."""


class TallyUnreachable(TallyError):
    """The HTTP server isn't responding (Tally not running, port closed)."""


class TallyResponseError(TallyError):
    """Tally responded with a non-200 status."""

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(
            f"Tally responded {status_code}"
            + (f": {body[:200]}" if body else "")
        )
        self.status_code = status_code
        self.body = body


class TallyParseError(TallyError):
    """Tally responded with malformed XML."""


# ---------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEntryInput:
    """One Dr/Cr line in a voucher being posted."""

    ledger_name: str
    amount: Decimal
    entry_type: str  # 'Dr' or 'Cr'


@dataclass(frozen=True)
class VoucherInput:
    """Payload accepted by `post_voucher`.

    `entries` is N-line by design (the salvaged 2-line shape was a
    Phase-0-blocker). The caller — backend voucher_dispatcher in
    P0.26 — assembles this from a `Voucher` + its `LedgerEntry` rows.
    """

    voucher_type: str
    voucher_date: date
    voucher_number: str
    party_name: str
    narration: str
    entries: list[LedgerEntryInput]


@dataclass(frozen=True)
class LedgerMaster:
    name: str
    parent_group: str
    gstin: str | None = None


@dataclass(frozen=True)
class GroupMaster:
    name: str
    parent: str


@dataclass(frozen=True)
class TrialBalanceRow:
    name: str
    closing_balance: Decimal


@dataclass(frozen=True)
class OutstandingItem:
    bill_name: str
    amount: Decimal
    due_date: str | None


@dataclass(frozen=True)
class LedgerVoucherRow:
    """A row in a ledger's voucher list (get_ledger)."""

    voucher_id: str
    voucher_type: str
    voucher_number: str
    voucher_date: date
    amount: Decimal
    narration: str


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _fiscal_year_start(today: date | None = None) -> str:
    """First day of the current Indian fiscal year (1 April), as YYYY-MM-DD."""
    today = today or date.today()
    year = today.year if today.month >= 4 else today.year - 1
    return f"{year}-04-01"


def _fiscal_year_end(today: date | None = None) -> str:
    today = today or date.today()
    year = today.year if today.month < 4 else today.year + 1
    return f"{year}-03-31"


def _get_text(element: ET.Element, tag: str, default: str = "") -> str:
    child = element.find(tag)
    return child.text if child is not None and child.text else default


def _parse_tally_date(tally_date: str) -> date:
    """Tally's YYYYMMDD → Python date. Falls back to today on bad input."""
    if tally_date and len(tally_date) == 8:
        try:
            return datetime.strptime(tally_date, "%Y%m%d").date()
        except ValueError:
            pass
    return date.today()


def _decimal(text: str | None) -> Decimal:
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(text)
    except (ValueError, ArithmeticError):
        return Decimal("0.00")


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------


class TallyClient:
    """Talks XML over HTTP to a local TallyPrime instance.

    Construct one per long-lived process. Methods are async; the
    underlying `httpx.AsyncClient` is created per call to keep this
    class trivially picklable for use under PyInstaller.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9000,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.headers = {"Content-Type": "application/xml"}

    # ------------------------------------------------------------------
    # ping
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.base_url,
                    content="<ENVELOPE></ENVELOPE>",
                    headers=self.headers,
                )
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # Internal: send + base-class error handling
    # ------------------------------------------------------------------

    async def _post_xml(self, xml_request: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.base_url,
                    content=xml_request,
                    headers=self.headers,
                )
        except httpx.HTTPError as exc:
            raise TallyUnreachable(str(exc)) from exc
        if response.status_code != 200:
            raise TallyResponseError(response.status_code, response.text)
        return response.text

    # ------------------------------------------------------------------
    # get_ledger (party transactions)
    # ------------------------------------------------------------------

    async def get_ledger(
        self,
        party_name: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        from_date = from_date or _fiscal_year_start()
        to_date = to_date or _fiscal_year_end()

        xml = (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST>"
            "<TYPE>Data</TYPE>"
            "<ID>Ledger Vouchers</ID>"
            "</HEADER>"
            "<BODY><DESC>"
            "<STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            f"<SVFROMDATE>{from_date}</SVFROMDATE>"
            f"<SVTODATE>{to_date}</SVTODATE>"
            "</STATICVARIABLES>"
            "<DYNAMICVARIABLES>"
            f"<SVLEDGERNAME>{party_name}</SVLEDGERNAME>"
            "</DYNAMICVARIABLES>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_ledger_response(body, party_name)

    # ------------------------------------------------------------------
    # get_all_ledgers
    # ------------------------------------------------------------------

    async def get_all_ledgers(self) -> list[LedgerMaster]:
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST>"
            "<TYPE>Data</TYPE>"
            "<ID>Ledger</ID>"
            "</HEADER>"
            "<BODY><DESC>"
            "<STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            "</STATICVARIABLES>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_ledgers_list(body)

    # ------------------------------------------------------------------
    # get_all_groups
    # ------------------------------------------------------------------

    async def get_all_groups(self) -> list[GroupMaster]:
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST>"
            "<TYPE>Data</TYPE>"
            "<ID>Group</ID>"
            "</HEADER>"
            "<BODY><DESC>"
            "<STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            "</STATICVARIABLES>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_groups_list(body)

    # ------------------------------------------------------------------
    # post_voucher
    # ------------------------------------------------------------------

    async def post_voucher(self, voucher: VoucherInput) -> dict[str, Any]:
        """Send a voucher to Tally for creation.

        Builds an ImportData envelope with the N-line ledger entries
        the caller passed. Returns `{status, voucher_number}` on
        success; raises `TallyResponseError` / `TallyUnreachable` on
        failure.
        """
        xml = self._build_voucher_xml(voucher)
        body = await self._post_xml(xml)
        return {
            "status": "success",
            "voucher_number": voucher.voucher_number,
            "raw": body,
        }

    # ------------------------------------------------------------------
    # get_trial_balance
    # ------------------------------------------------------------------

    async def get_trial_balance(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[TrialBalanceRow]:
        from_date = from_date or _fiscal_year_start()
        to_date = to_date or _fiscal_year_end()

        xml = (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST>"
            "<TYPE>Data</TYPE>"
            "<ID>Trial Balance</ID>"
            "</HEADER>"
            "<BODY><DESC>"
            "<STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            f"<SVFROMDATE>{from_date}</SVFROMDATE>"
            f"<SVTODATE>{to_date}</SVTODATE>"
            "</STATICVARIABLES>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_trial_balance(body)

    # ------------------------------------------------------------------
    # get_outstanding
    # ------------------------------------------------------------------

    async def get_outstanding(
        self,
        party_type: str = "Sundry Debtors",
        as_of_date: str | None = None,
    ) -> list[OutstandingItem]:
        as_of_date = as_of_date or str(date.today())
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST>"
            "<TYPE>Data</TYPE>"
            "<ID>Outstanding Receivables</ID>"
            "</HEADER>"
            "<BODY><DESC>"
            "<STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            f"<SVFROMDATE>{_fiscal_year_start()}</SVFROMDATE>"
            f"<SVTODATE>{as_of_date}</SVTODATE>"
            "</STATICVARIABLES>"
            "<DYNAMICVARIABLES>"
            f"<SVLEDGERNAME>{party_type}</SVLEDGERNAME>"
            "</DYNAMICVARIABLES>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_outstanding(body)

    # ==================================================================
    # XML parsing helpers
    # ==================================================================

    def _parse_ledger_response(
        self, xml_string: str, party_name: str
    ) -> dict[str, Any]:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        rows: list[LedgerVoucherRow] = []
        for voucher in root.findall(".//VOUCHER"):
            rows.append(
                LedgerVoucherRow(
                    voucher_id=voucher.get("REMOTEID", ""),
                    voucher_type=voucher.get("VCHTYPE", ""),
                    voucher_number=_get_text(voucher, "VOUCHERNUMBER"),
                    voucher_date=_parse_tally_date(_get_text(voucher, "DATE")),
                    amount=_decimal(_get_text(voucher, "AMOUNT", "0")),
                    narration=_get_text(voucher, "NARRATION", ""),
                )
            )

        # Net closing = sum of positive (Dr) - sum of |negatives| (Cr).
        total_debit = sum(
            (r.amount for r in rows if r.amount > 0),
            start=Decimal("0"),
        )
        total_credit = sum(
            (abs(r.amount) for r in rows if r.amount < 0),
            start=Decimal("0"),
        )
        return {
            "party_name": party_name,
            "transactions": rows,
            "opening_balance": Decimal("0.00"),
            "closing_balance": total_debit - total_credit,
            "transaction_count": len(rows),
        }

    def _parse_ledgers_list(self, xml_string: str) -> list[LedgerMaster]:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        out: list[LedgerMaster] = []
        for ledger in root.findall(".//LEDGER"):
            name = _get_text(ledger, "NAME")
            if not name:
                continue
            out.append(
                LedgerMaster(
                    name=name,
                    parent_group=_get_text(ledger, "PARENT"),
                    gstin=_get_text(ledger, "REGISTRATIONTYPE", "") or None,
                )
            )
        return out

    def _parse_groups_list(self, xml_string: str) -> list[GroupMaster]:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        out: list[GroupMaster] = []
        for group in root.findall(".//GROUP"):
            name = _get_text(group, "NAME")
            if not name:
                continue
            out.append(
                GroupMaster(name=name, parent=_get_text(group, "PARENT"))
            )
        return out

    def _parse_trial_balance(
        self, xml_string: str
    ) -> list[TrialBalanceRow]:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        out: list[TrialBalanceRow] = []
        for ledger in root.findall(".//LEDGER"):
            name = _get_text(ledger, "NAME")
            if not name:
                continue
            out.append(
                TrialBalanceRow(
                    name=name,
                    closing_balance=_decimal(
                        _get_text(ledger, "CLOSINGBALANCE", "0")
                    ),
                )
            )
        return out

    def _parse_outstanding(self, xml_string: str) -> list[OutstandingItem]:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        out: list[OutstandingItem] = []
        for entry in root.findall(".//BILLALLOCATIONS.LIST"):
            name = _get_text(entry, "NAME")
            if not name:
                continue
            out.append(
                OutstandingItem(
                    bill_name=name,
                    amount=abs(_decimal(_get_text(entry, "AMOUNT", "0"))),
                    due_date=_get_text(entry, "BILLDATE") or None,
                )
            )
        return out

    # ==================================================================
    # Voucher XML builder
    # ==================================================================

    def _build_voucher_xml(self, v: VoucherInput) -> str:
        """Build a Tally Import-Data envelope from a `VoucherInput`."""
        date_str = v.voucher_date.strftime("%Y%m%d")
        entries_xml = "".join(
            self._build_entry_xml(e) for e in v.entries
        )
        return (
            "<ENVELOPE>"
            "<HEADER>"
            "<TALLYREQUEST>Import Data</TALLYREQUEST>"
            "</HEADER>"
            "<BODY><IMPORTDATA>"
            "<REQUESTDESC>"
            "<REPORTNAME>Vouchers</REPORTNAME>"
            "</REQUESTDESC>"
            "<REQUESTDATA>"
            '<TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<VOUCHER VCHTYPE="{v.voucher_type}" ACTION="Create">'
            f"<DATE>{date_str}</DATE>"
            f"<VOUCHERTYPENAME>{v.voucher_type}</VOUCHERTYPENAME>"
            f"<VOUCHERNUMBER>{v.voucher_number}</VOUCHERNUMBER>"
            f"<PARTYLEDGERNAME>{v.party_name}</PARTYLEDGERNAME>"
            f"{entries_xml}"
            f"<NARRATION>{v.narration}</NARRATION>"
            "</VOUCHER>"
            "</TALLYMESSAGE>"
            "</REQUESTDATA>"
            "</IMPORTDATA></BODY></ENVELOPE>"
        )

    def _build_entry_xml(self, e: LedgerEntryInput) -> str:
        is_deemed_positive = "Yes" if e.entry_type == "Dr" else "No"
        amount = e.amount if e.entry_type == "Dr" else -e.amount
        return (
            "<ALLLEDGERENTRIES.LIST>"
            f"<LEDGERNAME>{e.ledger_name}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE>{is_deemed_positive}</ISDEEMEDPOSITIVE>"
            f"<AMOUNT>{amount}</AMOUNT>"
            "</ALLLEDGERENTRIES.LIST>"
        )
