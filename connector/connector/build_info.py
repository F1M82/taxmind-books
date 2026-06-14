"""Build provenance accessor.

At build time, ``installer/build_exe.py`` generates a sibling
``_build_info.py`` (gitignored) holding the git short-SHA, a dirty flag,
and a UTC build timestamp, which PyInstaller bundles into the frozen
``.exe``. This module imports those constants with a fallback so that a
source checkout / test run (where ``_build_info.py`` does not exist)
still imports cleanly and reports ``sha == "dev"``.

Only a built binary carries a real SHA; running from source is always
``dev``.
"""

from __future__ import annotations

from connector import __version__

try:  # generated at build time, gitignored
    from connector._build_info import (  # type: ignore[import-untyped]
        BUILD_DIRTY,
        BUILD_SHA,
        BUILT_AT,
    )
except ImportError:  # source checkout / tests: not built
    BUILD_SHA = "dev"
    BUILD_DIRTY = False
    BUILT_AT = None


def format_version() -> str:
    """One-line human version string for the ``--version`` flag."""
    dirty = " dirty" if BUILD_DIRTY else ""
    built = BUILT_AT if BUILT_AT else "unbuilt"
    return (
        f"TaxMindBooksConnector {__version__} "
        f"(build {BUILD_SHA}{dirty}, built {built})"
    )
