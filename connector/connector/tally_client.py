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
    post_voucher, get_trial_balance, get_outstanding,
    approve_optional_voucher, reject_optional_voucher
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

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


class TallyImportRejected(TallyError):
    """Tally rejected an ImportData request structurally.

    Raised by `_post_and_validate_import` when the response envelope
    indicates the expected counter (CREATED / ALTERED / DELETED) is 0
    and <EXCEPTIONS> >= 1. Carries the <LINEERROR> text + the exception
    count + the raw body for diagnostics. Connector-side; the connector's
    `dispatch_command` catches via the existing TallyError branch and
    wraps as {status:"error", retryable: False} (operator action required).
    """

    def __init__(
        self,
        line_error: str | None,
        exceptions: int,
        raw_body: str,
    ) -> None:
        super().__init__(
            line_error
            or f"Tally rejected import ({exceptions} exception(s))"
        )
        self.line_error = line_error
        self.exceptions = exceptions
        self.raw_body = raw_body


class TallyAmbiguousResponse(TallyError):
    """Tally returned a response that matches neither strict success
    nor strict rejection.

    Strict success: expected counter >= 1 AND EXCEPTIONS == 0 AND no
    LINEERROR. Strict rejection: expected counter == 0 AND
    EXCEPTIONS >= 1. Anything else (partial success, missing CREATED,
    zero-everything, etc.) raises this.

    Treated as retryable on the wire — the shape may be a transient
    TallyPrime version drift. Surface for investigation rather than
    silently bucketing as success or failure.
    """

    def __init__(
        self,
        parsed: ImportResponse,
        raw_body: str,
    ) -> None:
        super().__init__(
            f"Tally returned ambiguous response: created={parsed.created}, "
            f"altered={parsed.altered}, deleted={parsed.deleted}, "
            f"exceptions={parsed.exceptions}, "
            f"line_error={parsed.line_error!r}"
        )
        self.parsed = parsed
        self.raw_body = raw_body


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

    `as_optional` (v1.2) emits `<ISOPTIONAL>Yes</ISOPTIONAL>` so Tally
    posts the voucher in the Optional state; a later
    `approve_optional_voucher` call promotes it to Regular.
    """

    voucher_type: str
    voucher_date: date
    voucher_number: str
    party_name: str
    narration: str
    entries: list[LedgerEntryInput]
    as_optional: bool = False


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


@dataclass(frozen=True)
class ImportResponse:
    """Parsed counts from a TallyPrime ImportData response envelope.

    Tally returns the same envelope shape for ImportData operations:
    <CREATED>, <ALTERED>, <DELETED>, <EXCEPTIONS>, <LASTVCHID>, and
    optionally <LINEERROR>. The expected non-zero counter depends on
    the operation (Create -> CREATED, Alter -> ALTERED, Delete -> DELETED).
    """

    created: int
    altered: int
    deleted: int
    exceptions: int
    last_vch_id: str | None
    line_error: str | None
    raw_body: str


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


def _strip_tally_ctrl(text: str) -> str:
    # Tally prefixes reserved master names with an ASCII control char
    # (commonly \x04, the EOT marker) to distinguish system-defined groups
    # like "Primary" from user-created ones. We strip leading control chars
    # for storage; embedded ones are left alone (none are expected in
    # well-formed names).
    return text.lstrip("".join(chr(c) for c in range(0x20))).strip()


# `&#N;` numeric character references where N is an XML-1.0-forbidden
# control codepoint. Tally emits `&#4;` (EOT) inline to mark reserved
# masters; expat rejects it. We drop those refs at the response boundary
# so downstream parsing is well-formed. Permitted control chars 0x09
# (tab), 0x0A (LF), 0x0D (CR) and everything ≥ 0x20 are left intact.
_BAD_XML_REF_RE = re.compile(r"&#(?:(\d+)|[xX]([0-9a-fA-F]+));")


def _sanitize_tally_xml(body: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        n = int(m.group(1)) if m.group(1) else int(m.group(2), 16)
        if n in (0x09, 0x0A, 0x0D) or n >= 0x20:
            return m.group(0)
        return ""

    return _BAD_XML_REF_RE.sub(_replace, body)


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


def _parse_import_response(body: str) -> ImportResponse:
    """Parse a TallyPrime ImportData response envelope.

    Tolerant to missing optional elements (no <LINEERROR> on success;
    no <LASTVCHID> on some Alter/Delete responses). Counters that fail
    to parse as int default to 0 — the strict-shape predicate in
    `_post_and_validate_import` then routes a 0-counter through the
    rejection or ambiguous branches.

    Raises:
        TallyParseError: on malformed XML.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise TallyParseError(str(exc)) from exc

    def _int(tag: str) -> int:
        text = _get_text(root, tag, "0")
        try:
            return int(text)
        except ValueError:
            return 0

    def _str_or_none(tag: str) -> str | None:
        text = _get_text(root, tag, "")
        return text or None

    return ImportResponse(
        created=_int("CREATED"),
        altered=_int("ALTERED"),
        deleted=_int("DELETED"),
        exceptions=_int("EXCEPTIONS"),
        last_vch_id=_str_or_none("LASTVCHID"),
        line_error=_str_or_none("LINEERROR"),
        raw_body=body,
    )


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
        return _sanitize_tally_xml(response.text)

    async def _post_and_validate_import(
        self,
        xml_request: str,
        *,
        expect: Literal["created", "altered", "deleted"],
    ) -> ImportResponse:
        """Post an ImportData envelope and validate the response shape.

        Wraps `_post_xml` and adds the import-only parse-and-raise that
        `_post_xml` itself can't do: export-data callers (`get_ledger`,
        `get_all_ledgers`, `get_all_groups`, `get_trial_balance`,
        `get_outstanding`) share `_post_xml` and their response envelopes
        have no <CREATED> element. Single choke point for `post_voucher`,
        `approve_optional_voucher`, `reject_optional_voucher` — future
        ImportData methods should call this instead of raw `_post_xml`.

        Strict success: expected counter >= 1 AND <EXCEPTIONS> == 0
        AND no <LINEERROR>. Strict rejection: expected counter == 0
        AND <EXCEPTIONS> >= 1. Anything else (partial success, missing
        CREATED element, etc.) raises TallyAmbiguousResponse.

        Raises:
            TallyImportRejected: strict-rejection envelope.
            TallyAmbiguousResponse: response matches neither strict
                success nor strict rejection.
            TallyParseError: malformed XML (via `_parse_import_response`).
            TallyUnreachable / TallyResponseError: from `_post_xml`.
        """
        body = await self._post_xml(xml_request)
        parsed = _parse_import_response(body)
        expected_counter = getattr(parsed, expect)
        if (
            expected_counter >= 1
            and parsed.exceptions == 0
            and not parsed.line_error
        ):
            return parsed
        if expected_counter == 0 and parsed.exceptions >= 1:
            raise TallyImportRejected(
                parsed.line_error, parsed.exceptions, body
            )
        raise TallyAmbiguousResponse(parsed, body)

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
        # Tally rejects the bare `<TYPE>Data</TYPE><ID>Ledger</ID>` form
        # ("Unknown Request, cannot be processed") because that idiom
        # exports a SINGLE ledger and needs an SVLEDGERNAME variable.
        # For "list all ledgers" the canonical idiom is a TDL Collection
        # request with an in-line collection definition.
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
              "<VERSION>1</VERSION>"
              "<TALLYREQUEST>Export</TALLYREQUEST>"
              "<TYPE>Collection</TYPE>"
              "<ID>TaxMindLedgers</ID>"
            "</HEADER>"
            "<BODY><DESC>"
              "<STATICVARIABLES>"
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
              "</STATICVARIABLES>"
              "<TDL>"
                "<TDLMESSAGE>"
                  '<COLLECTION NAME="TaxMindLedgers" ISMODIFY="No">'
                    "<TYPE>Ledger</TYPE>"
                    "<NATIVEMETHOD>Name</NATIVEMETHOD>"
                    "<NATIVEMETHOD>Parent</NATIVEMETHOD>"
                    "<NATIVEMETHOD>PartyGSTIN</NATIVEMETHOD>"
                  "</COLLECTION>"
                "</TDLMESSAGE>"
              "</TDL>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_ledgers_list(body)

    # ------------------------------------------------------------------
    # get_all_groups
    # ------------------------------------------------------------------

    async def get_all_groups(self) -> list[GroupMaster]:
        # See get_all_ledgers — same idiom (TDL Collection) for the same
        # reason. The bare Data/Group form is rejected by TallyPrime.
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
              "<VERSION>1</VERSION>"
              "<TALLYREQUEST>Export</TALLYREQUEST>"
              "<TYPE>Collection</TYPE>"
              "<ID>TaxMindGroups</ID>"
            "</HEADER>"
            "<BODY><DESC>"
              "<STATICVARIABLES>"
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
              "</STATICVARIABLES>"
              "<TDL>"
                "<TDLMESSAGE>"
                  '<COLLECTION NAME="TaxMindGroups" ISMODIFY="No">'
                    "<TYPE>Group</TYPE>"
                    "<NATIVEMETHOD>Name</NATIVEMETHOD>"
                    "<NATIVEMETHOD>Parent</NATIVEMETHOD>"
                  "</COLLECTION>"
                "</TDLMESSAGE>"
              "</TDL>"
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
        the caller passed. Validates the response envelope's <CREATED>
        counter >= 1 (strict success).

        `tally_voucher_guid` is returned as `None` — Layer C (durable
        Tally GUID via REMOTEID or <LASTVCHID>) is deferred per
        bug_books_004 'Layer A fix design'.

        Raises:
            TallyImportRejected: Tally rejected the create.
            TallyAmbiguousResponse: response envelope shape unknown.
            TallyUnreachable / TallyResponseError: transport failures.
        """
        parsed = await self._post_and_validate_import(
            self._build_voucher_xml(voucher), expect="created"
        )
        return {
            "status": "success",
            "voucher_number": voucher.voucher_number,
            "as_optional": voucher.as_optional,
            "tally_voucher_guid": None,
            "raw": parsed.raw_body,
        }

    # ------------------------------------------------------------------
    # approve_optional_voucher  (v1.2)
    # ------------------------------------------------------------------

    async def approve_optional_voucher(
        self, voucher_guid: str
    ) -> dict[str, Any]:
        """Promote an Optional voucher to Regular in Tally.

        Issues an ACTION="Alter" against the voucher's REMOTEID that
        flips `<ISOPTIONAL>` from Yes to No. Validates the response
        envelope's <ALTERED> counter >= 1 (strict success). Idempotent
        for the Tally side: re-running against an already-Regular
        voucher returns ALTERED=1 again in TallyPrime's normal flow.

        Raises:
            TallyImportRejected: Tally refused the alter (e.g. unknown
                REMOTEID).
            TallyAmbiguousResponse: response envelope shape unknown.
            TallyUnreachable / TallyResponseError: transport failures.
        """
        parsed = await self._post_and_validate_import(
            self._build_alter_isoptional_xml(voucher_guid, optional=False),
            expect="altered",
        )
        return {
            "status": "success",
            "tally_voucher_guid": voucher_guid,
            "raw": parsed.raw_body,
        }

    # ------------------------------------------------------------------
    # reject_optional_voucher  (v1.2)
    # ------------------------------------------------------------------

    async def reject_optional_voucher(
        self, voucher_guid: str
    ) -> dict[str, Any]:
        """Delete an Optional voucher from Tally entirely.

        Issues an ACTION="Delete" against the voucher's REMOTEID.
        Validates the response envelope's <DELETED> counter >= 1
        (strict success). The caller is responsible for not invoking
        this on already-Regular vouchers (the backend gates that).

        Raises:
            TallyImportRejected: Tally refused the delete (e.g. unknown
                REMOTEID).
            TallyAmbiguousResponse: response envelope shape unknown.
            TallyUnreachable / TallyResponseError: transport failures.
        """
        parsed = await self._post_and_validate_import(
            self._build_delete_voucher_xml(voucher_guid), expect="deleted"
        )
        return {
            "status": "success",
            "tally_voucher_guid": voucher_guid,
            "raw": parsed.raw_body,
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
            # In real Tally TDL-Collection responses, `<LEDGER>` carries
            # NAME as an XML attribute; the inner `<NAME>` lives two levels
            # deep under `<LANGUAGENAME.LIST>` and `ET.find("NAME")` won't
            # reach it. PARTYGSTIN is the actual GSTIN field — the old
            # parser read REGISTRATIONTYPE which is the registration-type
            # enum (Regular / Composition / Consumer / Unregistered), not
            # the GSTIN.
            name = _strip_tally_ctrl(ledger.get("NAME", ""))
            if not name:
                continue
            parent = _strip_tally_ctrl(_get_text(ledger, "PARENT"))
            gstin = _get_text(ledger, "PARTYGSTIN", "").strip() or None
            out.append(
                LedgerMaster(
                    name=name,
                    parent_group=parent,
                    gstin=gstin,
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
            name = _strip_tally_ctrl(group.get("NAME", ""))
            if not name:
                continue
            parent = _strip_tally_ctrl(_get_text(group, "PARENT"))
            out.append(GroupMaster(name=name, parent=parent))
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
        # Tally treats absence of ISOPTIONAL as "No"; emit only when
        # we want the voucher posted as Optional.
        optional_xml = "<ISOPTIONAL>Yes</ISOPTIONAL>" if v.as_optional else ""
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
            f"{optional_xml}"
            f"{entries_xml}"
            f"<NARRATION>{v.narration}</NARRATION>"
            "</VOUCHER>"
            "</TALLYMESSAGE>"
            "</REQUESTDATA>"
            "</IMPORTDATA></BODY></ENVELOPE>"
        )

    def _build_alter_isoptional_xml(
        self, voucher_guid: str, *, optional: bool
    ) -> str:
        """Build an ACTION='Alter' envelope that flips ISOPTIONAL."""
        flag = "Yes" if optional else "No"
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
            f'<VOUCHER REMOTEID="{voucher_guid}" ACTION="Alter">'
            f"<ISOPTIONAL>{flag}</ISOPTIONAL>"
            "</VOUCHER>"
            "</TALLYMESSAGE>"
            "</REQUESTDATA>"
            "</IMPORTDATA></BODY></ENVELOPE>"
        )

    def _build_delete_voucher_xml(self, voucher_guid: str) -> str:
        """Build an ACTION='Delete' envelope for a voucher."""
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
            f'<VOUCHER REMOTEID="{voucher_guid}" ACTION="Delete"/>'
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
