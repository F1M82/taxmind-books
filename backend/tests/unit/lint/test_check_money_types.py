"""Tests for tools/lint/check_money_types.py.

The lint script lives at the repository root (`tools/lint/`), so we add
its parent to sys.path before importing it. These tests build small
fixture files in `tmp_path`, run the checker, and assert on the
violation list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TOOLS_DIR = _REPO_ROOT / "tools" / "lint"
sys.path.insert(0, str(_TOOLS_DIR))

import check_money_types as cmt  # noqa: E402


@pytest.fixture
def write_file(tmp_path: Path):  # type: ignore[no-untyped-def]
    def _write(name: str, source: str) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    return _write


def test_clean_file_has_no_violations(write_file) -> None:  # type: ignore[no-untyped-def]
    p = write_file(
        "clean.py",
        "from decimal import Decimal\n"
        "amount: Decimal = Decimal('100.00')\n"
        "balance: Decimal = Decimal(str(100))\n",
    )
    assert cmt.check_path(p) == []


def test_m001_flags_float_annotation_on_money_name(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file("m001.py", "amount: float = 1.5\n")
    violations = cmt.check_path(p)
    assert len(violations) == 1
    assert violations[0].code == "M001"
    assert "amount" in violations[0].message


def test_m001_ignores_float_on_non_money_name(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file("m001b.py", "ratio: float = 1.5\n")
    assert cmt.check_path(p) == []


def test_m001_flags_optional_float(write_file) -> None:  # type: ignore[no-untyped-def]
    p = write_file("m001c.py", "tax_total: float | None = None\n")
    violations = cmt.check_path(p)
    assert len(violations) == 1
    assert violations[0].code == "M001"


def test_m001_flags_function_arg_float(write_file) -> None:  # type: ignore[no-untyped-def]
    p = write_file(
        "m001d.py",
        "def post(gst_amount: float) -> None:\n    pass\n",
    )
    violations = cmt.check_path(p)
    assert any(v.code == "M001" for v in violations)


def test_m002_flags_wrong_numeric_precision(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "m002.py",
        "from sqlalchemy import Numeric\n"
        "MyMoney = Numeric(10, 4)\n",
    )
    violations = cmt.check_path(p)
    assert any(v.code == "M002" for v in violations)


def test_m002_accepts_correct_numeric_precision(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "m002b.py",
        "from sqlalchemy import Numeric\n"
        "MoneyColumn = Numeric(15, 2)\n",
    )
    assert cmt.check_path(p) == []


def test_m002_does_not_flag_non_money_numeric(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    """Non-money Numeric like confidence_score / gst_rate must pass."""
    p = write_file(
        "m002c.py",
        "from sqlalchemy import Numeric\n"
        "confidence_score = Numeric(4, 3)\n"
        "gst_rate = Numeric(5, 2)\n"
        "ratio = Numeric(8, 6)\n",
    )
    assert cmt.check_path(p) == []


def test_m002_does_not_flag_rate_score_suffixes(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    """Names ending in _rate / _score / _count are not money."""
    p = write_file(
        "m002d.py",
        "from sqlalchemy import Numeric\n"
        "tax_rate = Numeric(5, 2)\n"
        "tds_score = Numeric(4, 3)\n"
        "voucher_number = Numeric(8, 0)\n",
    )
    assert cmt.check_path(p) == []


def test_m003_flags_decimal_from_float_literal(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "m003.py",
        "from decimal import Decimal\nx = Decimal(1.1)\n",
    )
    violations = cmt.check_path(p)
    assert any(v.code == "M003" for v in violations)


def test_m003_accepts_decimal_from_string_literal(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "m003b.py",
        "from decimal import Decimal\nx = Decimal('1.1')\n",
    )
    assert cmt.check_path(p) == []


def test_m004_flags_paise_math(write_file) -> None:  # type: ignore[no-untyped-def]
    p = write_file("m004.py", "total_paise = total_amount * 100\n")
    violations = cmt.check_path(p)
    assert any(v.code == "M004" for v in violations)


def test_m005_flags_basemodel_in_schemas(tmp_path: Path) -> None:
    schemas_dir = tmp_path / "app" / "schemas"
    schemas_dir.mkdir(parents=True)
    p = schemas_dir / "bad.py"
    p.write_text(
        "from pydantic import BaseModel\nclass Bad(BaseModel):\n    pass\n",
        encoding="utf-8",
    )
    violations = cmt.check_path(p)
    assert any(v.code == "M005" for v in violations)


def test_m005_accepts_taxmindbooksbase_inheritance(tmp_path: Path) -> None:
    schemas_dir = tmp_path / "app" / "schemas"
    schemas_dir.mkdir(parents=True)
    p = schemas_dir / "good.py"
    p.write_text(
        "from app.schemas.common import TaxMindBooksBase\n"
        "class Good(TaxMindBooksBase):\n    pass\n",
        encoding="utf-8",
    )
    assert cmt.check_path(p) == []


def test_m005_does_not_flag_outside_schemas(tmp_path: Path) -> None:
    p = tmp_path / "models.py"
    p.write_text(
        "from pydantic import BaseModel\nclass M(BaseModel):\n    pass\n",
        encoding="utf-8",
    )
    # M005 is scoped to app/schemas/; this file is outside that.
    violations = [v for v in cmt.check_path(p) if v.code == "M005"]
    assert violations == []


def test_main_returns_nonzero_on_violations(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("amount: float = 1.0\n", encoding="utf-8")
    assert cmt.main([str(bad)]) == 1


def test_main_returns_zero_on_clean(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("name: str = 'hello'\n", encoding="utf-8")
    assert cmt.main([str(good)]) == 0


def test_app_module_passes_lint() -> None:
    """The actual app/ tree must pass the money-types lint."""
    app_dir = _REPO_ROOT / "backend" / "app"
    violations: list[cmt.Violation] = []
    for f in cmt.iter_python_files([app_dir]):
        violations.extend(cmt.check_path(f))
    assert violations == [], "\n".join(v.render() for v in violations)
