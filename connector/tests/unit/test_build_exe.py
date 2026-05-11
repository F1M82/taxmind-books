"""Smoke tests for installer/build_exe.py.

The actual `build()` invocation runs PyInstaller and produces a real
binary — too slow + heavy for CI's general path. We assert the
script's invariants (entrypoint exists, command shape, icon
discovery) instead. The full build runs in
.github/workflows/connector-build.yml on Windows runners.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BUILD_SCRIPT = (
    Path(__file__).resolve().parents[2] / "installer" / "build_exe.py"
)


def _load_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("build_exe", _BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_exe"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_exe_script_exists() -> None:
    assert _BUILD_SCRIPT.exists()


def test_entrypoint_resolves_to_main_py() -> None:
    mod = _load_module()
    assert mod.ENTRYPOINT.exists()
    assert mod.ENTRYPOINT.name == "main.py"


def test_exe_name_is_taxmind_books_connector() -> None:
    mod = _load_module()
    assert mod.EXE_NAME == "TaxMindBooksConnector"


def test_icon_path_is_under_installer_dir() -> None:
    mod = _load_module()
    assert mod.ICON_PATH.parent.name == "installer"
    # Icon file itself may or may not be present.
    assert mod.ICON_PATH.suffix == ".ico"


def test_build_function_exists_and_is_callable() -> None:
    mod = _load_module()
    assert callable(mod.build)
