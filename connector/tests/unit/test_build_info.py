"""Unit tests for build_info (Phase 0.5 connector .exe versioning).

The generated `connector/_build_info.py` is gitignored and absent in a
source checkout, so the accessor must fall back to a 'dev' sentinel. The
fallback is exercised deterministically by forcing the generated import to
fail (regardless of whether a local build happened to leave the file on
disk), then restoring module state.
"""

from __future__ import annotations

import importlib
import sys

import connector.build_info as bi
from connector import __version__


def test_fallback_sha_is_dev_when_generated_module_absent() -> None:
    saved = sys.modules.get("connector._build_info", "MISSING")
    # A None entry in sys.modules makes `import connector._build_info`
    # raise ImportError -> the accessor takes its fallback branch.
    sys.modules["connector._build_info"] = None  # type: ignore[assignment]
    try:
        importlib.reload(bi)
        assert bi.BUILD_SHA == "dev"
        assert bi.BUILD_DIRTY is False
        assert bi.BUILT_AT is None
    finally:
        if saved == "MISSING":
            sys.modules.pop("connector._build_info", None)
        else:
            sys.modules["connector._build_info"] = saved  # type: ignore[assignment]
        importlib.reload(bi)  # restore real (file-based or fallback) state


def test_format_version_shape() -> None:
    s = bi.format_version()
    assert s.startswith("TaxMindBooksConnector ")
    assert __version__ in s
    # build token is present in parentheses
    assert "build " in s
