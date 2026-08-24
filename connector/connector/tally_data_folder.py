"""Read-only discovery of Tally company directories."""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class TallyDataFolderError(Exception):
    """The configured Tally data folder cannot be scanned."""


@dataclass(frozen=True)
class TallyCompanyDiscovery:
    """Metadata with identity kept separate from the display name."""

    identifier: str
    name: str
    gstin: str | None = None
    financial_year_start: date | None = None
    guid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tally_company_identifier": self.identifier,
            "tally_company_name": self.name,
            "gstin": self.gstin,
            "financial_year_start": (
                self.financial_year_start.isoformat()
                if self.financial_year_start else None
            ),
        }
        if self.guid:
            result["tally_company_guid"] = self.guid
        return result


def _value(text: str, *names: str) -> str | None:
    for name in names:
        match = re.search(
            rf"<[^>]*{re.escape(name)}[^>]*>(.*?)</[^>]*{re.escape(name)}>",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            if value:
                return value
        match = re.search(
            rf"\b{re.escape(name)}\s*[=:]\s*([^\r\n]+)", text,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1).strip().strip("\"'")
            if value:
                return value
    return None


def _extract_company_from_utf16(data: bytes) -> dict[str, str | None]:
    """Extract company identity from a TallyPrime Company.NNNN binary file.

    TallyPrime 7.x (version 1800) stores company metadata in UTF-16-LE
    encoded binary files named ``Company.<version>``.  The file embeds the
    company name, GSTIN, GUID and other fields as interleaved strings.
    We decode the full file as UTF-16-LE and extract the first occurrences
    of recognizable patterns.
    """
    text = data.decode("utf-16-le", errors="ignore")

    # Extract all readable ASCII-ish strings (≥ 4 chars)
    readable = re.findall(r"[\u0020-\u007e\u0900-\u097f]{4,}", text)

    result: dict[str, str | None] = {}

    # Company name: first substantial string (stripped of leading control
    # chars like 'Z' or '&' that Tally prefixes to mark it as the name).
    for s in readable:
        cleaned = s.strip(" ").lstrip("&Z")
        # Skip strings that look like GUIDs, numbers, or system keywords
        if re.match(r"^[0-9a-f-]{20,}$", cleaned, re.I):
            continue
        if re.match(r"^\d+$", cleaned):
            continue
        if len(cleaned) > 3:
            result.setdefault("name", cleaned)
            break

    # GUID: looks like a UUID
    for s in readable:
        m = re.match(r"[a-f0-9-]{20,}", s, re.I)
        if m and "-" in s:
            result.setdefault("guid", s.strip())
            break

    # GSTIN: 15-char alphanumeric matching Indian GSTIN format
    for s in readable:
        if re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", s):
            result["gstin"] = s
            break

    return result


def _read_company(directory: Path) -> TallyCompanyDiscovery | None:
    # --- Strategy 1: Manager.<version> file (Tally < 7.x) ---
    # TallyPrime stores the metadata file as Manager.<version> where
    # <version> varies by edition: 500 (older), 1800 (newer TallyPrime).
    # These files are plain-text / XML-like for older versions.
    metadata = next(
        (
            p
            for p in (
                directory / "manager.500",
                directory / "Manager.500",
            )
            if p.is_file()
        ),
        None,
    )
    if metadata is not None:
        try:
            text = metadata.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
        else:
            name = _value(text, "NAME", "COMPANYNAME", "CompanyName")
            if name:
                fy = _value(text, "FINANCIALYEARSTART", "FYSTART", "FinancialYearStart")
                fy_date = None
                if fy:
                    with suppress(ValueError):
                        fy_date = date.fromisoformat(fy[:10].replace("/", "-"))
                return TallyCompanyDiscovery(
                    identifier=directory.name,
                    name=name,
                    gstin=_value(text, "GSTIN", "GSTNUMBER"),
                    financial_year_start=fy_date,
                    guid=_value(text, "GUID", "COMPANYGUID"),
                )

    # --- Strategy 2: Company.<version> binary file (TallyPrime 7.x+) ---
    # TallyPrime 7.x (version 1800) stores company metadata in UTF-16-LE
    # encoded binary files named Company.1800 instead of Manager.500.
    company_file = next(
        (
            p
            for p in (
                directory / "Company.1800",
                directory / "company.1800",
                directory / "Company.500",
            )
            if p.is_file()
        ),
        None,
    )
    if company_file is not None:
        try:
            raw = company_file.read_bytes()
        except OSError:
            return None
        extracted = _extract_company_from_utf16(raw)
        name = extracted.get("name")
        if name:
            fy_str = extracted.get("financial_year_start")
            fy_date = None
            if fy_str:
                with suppress(ValueError):
                    fy_date = date.fromisoformat(fy_str[:10].replace("/", "-"))
            return TallyCompanyDiscovery(
                identifier=directory.name,
                name=name,
                gstin=extracted.get("gstin"),
                financial_year_start=fy_date,
                guid=extracted.get("guid"),
            )

    return None


def list_companies(data_folder_path: str) -> list[TallyCompanyDiscovery]:
    """Enumerate numeric company directories and parse safe metadata."""
    root = Path(data_folder_path).expanduser()
    if not root.is_dir():
        raise TallyDataFolderError(
            f"Path '{data_folder_path}' does not exist or is not a directory."
        )
    companies: list[TallyCompanyDiscovery] = []
    for directory in sorted(root.iterdir(), key=lambda path: path.name):
        if directory.is_dir() and directory.name.isdigit():
            company = _read_company(directory)
            if company is not None:
                companies.append(company)
    return companies
