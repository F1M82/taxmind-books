"""Tests for tools/lint/check_audit_emit.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TOOLS_DIR = _REPO_ROOT / "tools" / "lint"
sys.path.insert(0, str(_TOOLS_DIR))

import check_audit_emit as cae  # noqa: E402


@pytest.fixture
def write_file(tmp_path: Path):  # type: ignore[no-untyped-def]
    def _write(name: str, source: str) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        return p

    return _write


# ---------------- Violations ----------------


def test_flags_service_method_that_mutates_without_emit(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "voucher_service.py",
        """
class VoucherService:
    def __init__(self, db, audit):
        self.db = db
        self.audit = audit

    def create(self, data):
        voucher = object()
        self.db.add(voucher)
        return voucher
""",
    )
    violations = cae.check_path(p)
    assert len(violations) == 1
    assert violations[0].function == "VoucherService.create"


def test_flags_method_that_calls_db_delete_without_emit(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "ledger_service.py",
        """
class LedgerService:
    def __init__(self, db, audit):
        self.db = db

    def remove(self, ledger):
        self.db.delete(ledger)
""",
    )
    violations = cae.check_path(p)
    assert len(violations) == 1


# ---------------- Clean cases ----------------


def test_method_that_calls_emit_passes(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "voucher_service.py",
        """
class VoucherService:
    def __init__(self, db, audit):
        self.db = db
        self.audit = audit

    def create(self, data):
        voucher = object()
        self.db.add(voucher)
        self.audit.emit(
            action="voucher.created",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value={},
        )
        return voucher
""",
    )
    assert cae.check_path(p) == []


def test_read_only_method_passes(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "voucher_service.py",
        """
class VoucherService:
    def __init__(self, db):
        self.db = db

    def get(self, voucher_id):
        return self.db.query(Voucher).get(voucher_id)
""",
    )
    assert cae.check_path(p) == []


def test_audit_exempt_comment_suppresses_violation(
    write_file,  # type: ignore[no-untyped-def]
) -> None:
    p = write_file(
        "service.py",
        """
class Service:
    def __init__(self, db):
        self.db = db

    def admin_only_purge(self):  # audit-exempt: handled by deletion task
        self.db.delete(some_object)
""",
    )
    assert cae.check_path(p) == []


# ---------------- main() ----------------


def test_main_returns_one_on_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad_service.py"
    bad.write_text(
        "class S:\n"
        "    def __init__(self, db):\n"
        "        self.db = db\n"
        "    def x(self):\n"
        "        self.db.add(object())\n",
        encoding="utf-8",
    )
    assert cae.main([str(bad)]) == 1


def test_main_returns_zero_on_clean(tmp_path: Path) -> None:
    good = tmp_path / "good_service.py"
    good.write_text(
        "class S:\n"
        "    def __init__(self, db, audit):\n"
        "        self.db = db\n"
        "        self.audit = audit\n"
        "    def x(self):\n"
        "        self.db.add(object())\n"
        "        self.audit.emit(action='voucher.created',\n"
        "                        entity_type='voucher',\n"
        "                        entity_id=None,\n"
        "                        old_value=None,\n"
        "                        new_value={})\n",
        encoding="utf-8",
    )
    assert cae.main([str(good)]) == 0


def test_main_zero_when_app_services_empty() -> None:
    """The actual app/services/ tree must currently pass (Phase 0 has no
    services yet; future tasks add them)."""
    services = _REPO_ROOT / "backend" / "app" / "services"
    if services.exists():
        violations: list[cae.Violation] = []
        for f in cae.iter_python_files([services]):
            violations.extend(cae.check_path(f))
        assert violations == [], "\n".join(v.render() for v in violations)
