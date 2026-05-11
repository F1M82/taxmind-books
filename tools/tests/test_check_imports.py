"""Tests for tools/lint/check_imports.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = _REPO_ROOT / "tools" / "lint"
sys.path.insert(0, str(_TOOLS_DIR))

import check_imports as ci  # noqa: E402


@pytest.fixture
def write_file(tmp_path: Path):  # type: ignore[no-untyped-def]
    def _write(rel: str, source: str) -> Path:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return p

    return _write


# ---------------- happy paths ----------------


def test_models_import_from_core_is_fine(write_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = write_file(
        "backend/app/models/x.py",
        "from app.core.money import money_column\n",
    )
    assert ci.check_path(p, tmp_path) == []


def test_services_import_from_models_is_fine(write_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = write_file(
        "backend/app/services/x.py",
        "from app.models.user import User\n",
    )
    assert ci.check_path(p, tmp_path) == []


def test_api_import_from_services_is_fine(write_file, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = write_file(
        "backend/app/api/v1/x.py",
        "from app.services.voucher_service import VoucherService\n",
    )
    # No rules for backend/app/api/ — it can import anything.
    assert ci.check_path(p, tmp_path) == []


# ---------------- violations ----------------


def test_models_importing_services_is_a_violation(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/models/bad.py",
        "from app.services.voucher_service import VoucherService\n",
    )
    vs = ci.check_path(p, tmp_path)
    assert len(vs) == 1
    assert "app.services" in vs[0].statement
    assert vs[0].forbidden == "app.services"


def test_models_importing_api_is_a_violation(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/models/bad.py",
        "from app.api.deps import get_active_company\n",
    )
    vs = ci.check_path(p, tmp_path)
    assert len(vs) == 1
    assert vs[0].forbidden == "app.api"


def test_schemas_importing_services_is_a_violation(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/schemas/bad.py",
        "from app.services.voucher_service import VoucherService\n",
    )
    vs = ci.check_path(p, tmp_path)
    assert any(v.forbidden == "app.services" for v in vs)


def test_core_importing_models_is_a_violation(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/core/bad.py",
        "from app.models.user import User\n",
    )
    vs = ci.check_path(p, tmp_path)
    assert any(v.forbidden == "app.models" for v in vs)


def test_connector_importing_backend_is_a_violation(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "connector/connector/bad.py",
        "from app.models.user import User\n",
    )
    vs = ci.check_path(p, tmp_path)
    assert any(v.forbidden == "app" for v in vs)


# ---------------- type-checking exception ----------------


def test_type_checking_imports_are_exempt(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/models/good.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.services.x import Y\n",
    )
    assert ci.check_path(p, tmp_path) == []


def test_imports_exempt_comment_suppresses(
    write_file,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    p = write_file(
        "backend/app/models/good.py",
        "from app.services.x import Y  # imports-exempt: late-bound singleton init\n",
    )
    assert ci.check_path(p, tmp_path) == []


# ---------------- main + repo state ----------------


def test_main_returns_one_on_violation(tmp_path: Path) -> None:
    bad = tmp_path / "backend" / "app" / "models" / "bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(
        "from app.services.x import y\n", encoding="utf-8"
    )
    # The main() walks backend/+connector/ under the repo-root we pass.
    assert ci.main([str(tmp_path)]) == 1


def test_main_returns_zero_on_clean(tmp_path: Path) -> None:
    # Empty repo → no violations.
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "connector").mkdir(parents=True)
    assert ci.main([str(tmp_path)]) == 0


def test_real_repo_is_clean() -> None:
    """The actual repo's import graph must satisfy the boundaries."""
    assert ci.main([str(_REPO_ROOT)]) == 0
