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
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx

from connector.tally_data_folder import (
    TallyCompanyDiscovery,
    TallyDataFolderError,
    list_companies,
)

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


class WrongCompanyOpen(TallyError):
    """The requested company is not the company currently open in Tally."""


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
    # BUG-004 Layer C: a client-assigned durable id stamped as the Tally
    # voucher's REMOTEID on Create. We set it to the backend voucher id so
    # the same handle drives later Alter/Delete (approve/reject) and is
    # returned as `tally_voucher_guid`. None → no REMOTEID emitted (legacy).
    remote_id: str | None = None


@dataclass(frozen=True)
class LedgerMaster:
    name: str
    parent_group: str
    gstin: str | None = None
    master_id: str | None = None


@dataclass(frozen=True)
class CompanyInfo:
    """The current Tally company's identity (Phase 7B).

    ``guid`` is the Tally company GUID read from the Company collection
    export — the durable external identity. ``name`` is the display name,
    captured separately; the GUID is never inferred from the name.
    """

    name: str
    guid: str | None = None


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
class VoucherExportEntry:
    """One Dr/Cr line of an exported (read-only) Tally voucher.

    `amount` keeps Tally's signed convention (Dr positive, Cr negative);
    `entry_type` is the derived 'Dr'/'Cr' label. Export/mirror-only — this
    type never drives a write back to Tally.

    `ledger_guid` is the referenced ledger's native Tally GUID. The
    voucher XML itself only carries the ledger NAME (`LEDGERNAME`); the
    GUID is enriched from the `get_all_ledgers` collection (verified
    `<GUID>` source) via an exact-name join — no field name is invented.
    It is None when the ledger list has no unambiguous match for the name.
    """

    ledger_name: str
    amount: Decimal
    entry_type: str
    ledger_guid: str | None = None


@dataclass(frozen=True)
class VoucherExportRow:
    """A read-only projection of one Tally voucher for the historical mirror.

    Produced by `TallyClient.get_vouchers` (P3.1). Carries Tally's durable
    identifiers (guid / remote_id / vchkey / master_id / alter_id / voucher_key)
    alongside the accounting payload. It is deliberately inert: nothing in the
    export path posts, alters, or deletes a voucher.
    """

    tally_guid: str | None
    remote_id: str | None
    vchkey: str | None
    master_id: str | None
    alter_id: str | None
    voucher_key: str | None
    voucher_type: str
    date: date
    voucher_number: str | None
    narration: str | None
    reference: str | None
    party_ledger_name: str | None
    is_cancelled: bool
    is_optional: bool
    is_deleted: bool
    entries: tuple[VoucherExportEntry, ...]


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
# Voucher export parsing (P3.1 — read-only historical mirror)
# ---------------------------------------------------------------------

# `<VOUCHER` immediately followed by whitespace or `>` — matches the real
# voucher element and the CMPINFO `<VOUCHER>N</VOUCHER>` counter, but NOT
# `<VOUCHERNUMBER>` / `<VOUCHERTYPENAME>` / `<VOUCHERKEY>`.
_VCH_OPEN_RE = re.compile(r"<VOUCHER(?=[\s>])", re.IGNORECASE)
# A *real* voucher opening tag carries attributes (REMOTEID/VCHTYPE/…); the
# CMPINFO counter (`<VOUCHER>2</VOUCHER>`) does not — this filters it out.
_VCH_REAL_RE = re.compile(r"(?is)^<VOUCHER\s+[A-Za-z]")
_VCH_CLOSE = "</VOUCHER>"


def _yesno(text: str | None) -> bool:
    return (text or "").strip().lower() == "yes"


class _VoucherBlockScanner:
    """Incremental extractor of complete ``<VOUCHER …>…</VOUCHER>`` blocks.

    Feeds arbitrary text chunks (as they arrive off the wire) and returns any
    whole voucher blocks that have completed. It holds at most the current
    in-progress block plus an ~8-char tail in memory, so a multi-megabyte
    export is never materialised at once. The CMPINFO ``<VOUCHER>N</VOUCHER>``
    counter is skipped (no attributes). Purely a text state machine — it never
    talks to Tally.
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while True:
            m = _VCH_OPEN_RE.search(self._buf)
            if m is None:
                # No opening tag left; keep only a short tail that could be a
                # partial "<VOUCHER" split across the next chunk boundary.
                if len(self._buf) > len("<VOUCHER"):
                    self._buf = self._buf[-len("<VOUCHER"):]
                break
            start = m.start()
            close = self._buf.find(_VCH_CLOSE, m.end())
            if close == -1:
                # Incomplete block: drop the consumed prefix, keep from the
                # open tag so the next chunk can complete it.
                self._buf = self._buf[start:]
                break
            end = close + len(_VCH_CLOSE)
            block = self._buf[start:end]
            self._buf = self._buf[end:]
            if _VCH_REAL_RE.match(block):
                out.append(block)
        return out


def _parse_voucher_block(block: str) -> VoucherExportRow:
    """Parse ONE ``<VOUCHER>`` block into a `VoucherExportRow`. Read-only.

    Sanitises Tally's XML-forbidden control refs first (shared with the rest
    of the client), then parses the self-contained block. Raises
    `TallyParseError` if the block is malformed after sanitisation.
    """
    # A voucher block can reference the ``UDF:`` namespace (Tally user-defined
    # fields, e.g. ``<UDF:USERDESCRIPTION.LIST>``) whose ``xmlns:UDF``
    # declaration lives on an ancestor in the full export, not on the
    # extracted ``<VOUCHER>``. Parsing the block standalone would fail with
    # "unbound prefix", so wrap it in a root that declares the namespace.
    # The namespaced descendants are ignored — we only read plain-named tags.
    wrapped = f'<TMVOUCHERWRAP xmlns:UDF="TallyUDF">{block}</TMVOUCHERWRAP>'
    try:
        root = ET.fromstring(_sanitize_tally_xml(wrapped))
    except ET.ParseError as exc:
        raise TallyParseError(str(exc)) from exc
    el = root.find("VOUCHER")
    if el is None:
        raise TallyParseError("no <VOUCHER> element in block")

    entries: list[VoucherExportEntry] = []
    for le in el.findall("ALLLEDGERENTRIES.LIST"):
        name = _get_text(le, "LEDGERNAME").strip()
        if not name:
            continue
        amount = _decimal(_get_text(le, "AMOUNT", "0"))
        deemed = le.find("ISDEEMEDPOSITIVE")
        if deemed is not None and (deemed.text or "").strip():
            entry_type = "Dr" if _yesno(deemed.text) else "Cr"
        else:
            entry_type = "Dr" if amount >= 0 else "Cr"
        entries.append(
            VoucherExportEntry(
                ledger_name=name, amount=amount, entry_type=entry_type
            )
        )

    def _opt(tag: str) -> str | None:
        return _get_text(el, tag).strip() or None

    return VoucherExportRow(
        tally_guid=_opt("GUID"),
        remote_id=el.get("REMOTEID"),
        vchkey=el.get("VCHKEY"),
        master_id=_opt("MASTERID"),
        alter_id=_opt("ALTERID"),
        voucher_key=_opt("VOUCHERKEY"),
        voucher_type=_get_text(el, "VOUCHERTYPENAME").strip()
        or (el.get("VCHTYPE") or ""),
        date=_parse_tally_date(_get_text(el, "DATE")),
        voucher_number=_opt("VOUCHERNUMBER"),
        narration=_opt("NARRATION"),
        reference=_opt("REFERENCE"),
        party_ledger_name=_opt("PARTYLEDGERNAME"),
        is_cancelled=_yesno(_get_text(el, "ISCANCELLED")),
        is_optional=_yesno(_get_text(el, "ISOPTIONAL")),
        is_deleted=_yesno(_get_text(el, "ISDELETED")),
        entries=tuple(entries),
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
        data_folder_path: str | None = None,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.headers = {"Content-Type": "application/xml"}
        self.data_folder_path = data_folder_path

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
                    "<NATIVEMETHOD>GUID</NATIVEMETHOD>"
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
    # get_company_info  (P3.7 Phase 7B — company identity capture)
    # ------------------------------------------------------------------

    async def get_company_info(self) -> CompanyInfo:
        # Company collection Export. The company GUID is the durable external
        # identity and the ONLY key the backend trusts for automatic ledger
        # attachment. Name is captured separately and never used to infer the
        # GUID.
        xml = (
            "<ENVELOPE>"
            "<HEADER>"
              "<VERSION>1</VERSION>"
              "<TALLYREQUEST>Export</TALLYREQUEST>"
              "<TYPE>Collection</TYPE>"
              "<ID>TaxMindCompany</ID>"
            "</HEADER>"
            "<BODY><DESC>"
              "<STATICVARIABLES>"
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
              "</STATICVARIABLES>"
              "<TDL>"
                "<TDLMESSAGE>"
                  '<COLLECTION NAME="TaxMindCompany" ISMODIFY="No">'
                    "<TYPE>Company</TYPE>"
                    "<FETCH>Name,GUID</FETCH>"
                  "</COLLECTION>"
                "</TDLMESSAGE>"
              "</TDL>"
            "</DESC></BODY></ENVELOPE>"
        )
        body = await self._post_xml(xml)
        return self._parse_company_info(body)

    def list_tally_companies(self) -> list[TallyCompanyDiscovery]:
        if not self.data_folder_path:
            raise ValueError("TALLY_DATA_FOLDER_PATH is not configured")
        return list_companies(self.data_folder_path)

    async def get_active_tally_company(self) -> dict[str, Any]:
        """Return active company, matching its verified GUID to discovery."""
        try:
            company = await self.get_company_info()
        except TallyError:
            return {
                "tally_running": False,
                "active_company_identifier": None,
                "active_company_name": None,
                "tally_company_guid": None,
                "tally_company_identifier": None,
                "tally_company_name": None,
            }
        identifier = None
        if self.data_folder_path and company.guid:
            try:
                identifier = next(
                    (
                        item.identifier
                        for item in self.list_tally_companies()
                        if item.guid and item.guid == company.guid
                    ),
                    None,
                )
            except TallyDataFolderError:
                identifier = None
        return {
            "tally_running": True,
            "tally_company_guid": company.guid,
            "active_company_identifier": identifier,
            "active_company_name": company.name,
            "tally_company_identifier": identifier,
            "tally_company_name": company.name,
        }

    def _parse_company_info(self, xml_string: str) -> CompanyInfo:
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as exc:
            raise TallyParseError(str(exc)) from exc

        company = root.find(".//COMPANY")
        if company is None:
            raise TallyParseError("no <COMPANY> element in Tally response")
        name = _strip_tally_ctrl(company.get("NAME", ""))
        guid = _get_text(company, "GUID", "").strip() or None
        return CompanyInfo(name=name, guid=guid)

    # ------------------------------------------------------------------
    # get_vouchers  (P3.1 — read-only historical export)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_get_vouchers_xml(from_date: str, to_date: str) -> str:
        """Build a READ-ONLY, date-scoped Voucher COLLECTION export request.

        Scoping uses a TDL date FILTER with the requested dates embedded as
        literals — the only form empirically confirmed (P3.0 spike) to honour
        the range in TallyPrime; ``@@SVFromDate`` does not resolve inside a
        collection FILTER, so it cannot be used here. `from_date`/`to_date`
        are Tally ``YYYYMMDD`` strings.

        This is an Export request: it contains no ``<IMPORTDATA>``, no
        ``<TALLYMESSAGE>``, and no ``ACTION`` attribute — by construction it
        can never create, alter, or delete anything in Tally.
        """
        formula = (
            f'$Date &gt;= $$Date:"{from_date}" '
            f'AND $Date &lt;= $$Date:"{to_date}"'
        )
        return (
            "<ENVELOPE><HEADER><VERSION>1</VERSION>"
            "<TALLYREQUEST>Export Data</TALLYREQUEST><TYPE>Collection</TYPE>"
            "<ID>TaxMindVouchers</ID></HEADER><BODY><DESC><STATICVARIABLES>"
            "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
            "</STATICVARIABLES><TDL><TDLMESSAGE>"
            '<COLLECTION NAME="TaxMindVouchers" ISINITIALIZE="Yes">'
            "<TYPE>Voucher</TYPE>"
            "<FETCH>Date,VoucherTypeName,VoucherNumber,Reference,Narration,"
            "PartyLedgerName,Amount,GUID,RemoteId,MasterId,AlterId,VoucherKey,"
            "IsCancelled,IsOptional,IsDeleted,AllLedgerEntries</FETCH>"
            "<FILTER>TaxMindDateFilter</FILTER></COLLECTION>"
            '<SYSTEM TYPE="Formula" NAME="TaxMindDateFilter">'
            f"{formula}</SYSTEM>"
            "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
        )

    async def get_vouchers(
        self, from_date: str, to_date: str
    ) -> list[VoucherExportRow]:
        """Export vouchers dated within ``[from_date, to_date]`` (YYYYMMDD).

        READ-ONLY. Streams the HTTP response and parses one ``<VOUCHER>``
        block at a time (`_VoucherBlockScanner`), so a large date window is
        never fully materialised in memory. Returns the parsed rows in the
        order Tally emits them. The caller paginates by choosing the window.

        Raises:
            TallyUnreachable: transport failure reaching Tally.
            TallyResponseError: Tally returned a non-200 status.
            TallyParseError: a voucher block is malformed after sanitisation.
        """
        xml = self._build_get_vouchers_xml(from_date, to_date)
        scanner = _VoucherBlockScanner()
        rows: list[VoucherExportRow] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client, (
                client.stream(
                    "POST",
                    self.base_url,
                    content=xml,
                    headers=self.headers,
                )
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise TallyResponseError(
                        response.status_code,
                        body.decode("utf-8", errors="replace"),
                    )
                async for chunk in response.aiter_text():
                    for block in scanner.feed(chunk):
                        rows.append(_parse_voucher_block(block))
        except httpx.HTTPError as exc:
            raise TallyUnreachable(str(exc)) from exc
        return rows

    @staticmethod
    def enrich_ledger_guids(
        rows: list[VoucherExportRow], ledgers: list[LedgerMaster]
    ) -> list[VoucherExportRow]:
        """Attach each entry's ledger GUID from the ledger master list.

        The voucher export XML carries only `LEDGERNAME` per line; the
        ledger's native GUID is available from `get_all_ledgers` (the
        verified `<GUID>` field, surfaced as `LedgerMaster.master_id`).
        This join is exact on normalized name and only fills a GUID when
        exactly one ledger matches — an ambiguous or absent name leaves
        `ledger_guid` None so the downstream reconciler flags manual
        review rather than guessing. No fuzzy matching.
        """
        guid_by_name: dict[str, str] = {}
        ambiguous: set[str] = set()
        for led in ledgers:
            key = led.name.strip().lower()
            if not key:
                continue
            if led.master_id is None:
                continue
            if key in guid_by_name:
                ambiguous.add(key)
                continue
            guid_by_name[key] = led.master_id
        for key in ambiguous:
            guid_by_name.pop(key, None)

        enriched: list[VoucherExportRow] = []
        for row in rows:
            new_entries = tuple(
                replace(
                    entry,
                    ledger_guid=guid_by_name.get(
                        entry.ledger_name.strip().lower()
                    ),
                )
                for entry in row.entries
            )
            enriched.append(replace(row, entries=new_entries))
        return enriched

    # ------------------------------------------------------------------
    # post_voucher
    # ------------------------------------------------------------------

    async def post_voucher(self, voucher: VoucherInput) -> dict[str, Any]:
        """Send a voucher to Tally for creation.

        Builds an ImportData envelope with the N-line ledger entries
        the caller passed. Validates the response envelope's <CREATED>
        counter >= 1 (strict success).

        BUG-004 Layer C: when the caller supplies `remote_id`, it is
        stamped as the voucher's Tally REMOTEID on Create and echoed back
        as `tally_voucher_guid` — the durable, client-known handle the
        backend persists and later approve/reject (Alter/Delete by
        REMOTEID) rely on. Returns None only when no `remote_id` was given.

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
            "tally_voucher_guid": voucher.remote_id,
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
            master_id = _get_text(ledger, "GUID", "").strip() or None
            out.append(
                LedgerMaster(
                    name=name,
                    parent_group=parent,
                    gstin=gstin,
                    master_id=master_id,
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
        # BUG-004 Layer C: stamp a durable REMOTEID on Create so the voucher
        # carries a client-known handle (matches the Alter/Delete builders,
        # which reference the voucher by REMOTEID).
        remote_attr = f' REMOTEID="{v.remote_id}"' if v.remote_id else ""
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
            f'<VOUCHER{remote_attr} VCHTYPE="{v.voucher_type}" ACTION="Create">'
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
