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


def _read_company(directory: Path) -> TallyCompanyDiscovery | None:
    metadata = next(
        (p for p in (directory / "manager.500", directory / "Manager.500") if p.is_file()),
        None,
    )
    if metadata is None:
        return None
    try:
        text = metadata.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    name = _value(text, "NAME", "COMPANYNAME", "CompanyName")
    if not name:
        return None
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
