from __future__ import annotations

from pathlib import Path

import pytest

from connector.tally_data_folder import TallyDataFolderError, list_companies


def test_list_companies_uses_numeric_directory_and_preserves_guid(
    tmp_path: Path,
) -> None:
    company = tmp_path / "10000"
    company.mkdir()
    (company / "manager.500").write_text(
        "<COMPANY><NAME>Acme Traders</NAME><GUID>guid-1</GUID>"
        "<GSTIN>27AAAAA0000A1Z5</GSTIN>"
        "<FINANCIALYEARSTART>2026-04-01</FINANCIALYEARSTART></COMPANY>",
        encoding="utf-8",
    )
    (tmp_path / "Acme Traders").mkdir()
    assert [item.as_dict() for item in list_companies(str(tmp_path))] == [
        {
            "tally_company_identifier": "10000",
            "tally_company_name": "Acme Traders",
            "gstin": "27AAAAA0000A1Z5",
            "financial_year_start": "2026-04-01",
            "tally_company_guid": "guid-1",
        }
    ]


def test_list_companies_rejects_unreadable_root(tmp_path: Path) -> None:
    with pytest.raises(TallyDataFolderError):
        list_companies(str(tmp_path / "missing"))
