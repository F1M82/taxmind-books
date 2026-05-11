"""Build the connector as a Windows .exe via PyInstaller.

Local usage::

    cd connector
    pip install pyinstaller
    python installer/build_exe.py

CI usage: invoked from `.github/workflows/connector-build.yml`. The
built artifact lands at `connector/dist/TaxMindBooksConnector.exe`
and is uploaded as a workflow artifact + (on tag) attached to a
GitHub Release.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

CONNECTOR_DIR = Path(__file__).resolve().parents[1]
INSTALLER_DIR = Path(__file__).resolve().parent

EXE_NAME = "TaxMindBooksConnector"
ENTRYPOINT = CONNECTOR_DIR / "connector" / "main.py"
ICON_PATH = INSTALLER_DIR / "icon.ico"


def _check_pyinstaller_available() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed. Run: pip install pyinstaller",
            file=sys.stderr,
        )
        sys.exit(1)


def build() -> Path:
    """Run PyInstaller. Returns the path to the built .exe."""
    _check_pyinstaller_available()

    # Clean prior build artifacts.
    for d in ("build", "dist"):
        target = CONNECTOR_DIR / d
        if target.exists():
            shutil.rmtree(target)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--console",
        f"--name={EXE_NAME}",
        # Bundle the connector package so `import connector.*` works
        # from the frozen entry point.
        f"--paths={CONNECTOR_DIR}",
        # PyInstaller can't see celery/redis at import time without a
        # hint when the connector pulls them in transitively. Phase 0
        # connector doesn't use those, so no hidden imports yet.
        str(ENTRYPOINT),
    ]
    if ICON_PATH.exists():
        cmd.insert(-1, f"--icon={ICON_PATH}")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=CONNECTOR_DIR, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)

    exe_path = CONNECTOR_DIR / "dist" / f"{EXE_NAME}.exe"
    if not exe_path.exists():
        # Non-Windows platforms produce no .exe suffix. Still useful
        # for local sanity-check builds on macOS / Linux dev boxes.
        fallback = CONNECTOR_DIR / "dist" / EXE_NAME
        if fallback.exists():
            exe_path = fallback
    if not exe_path.exists():
        print(
            f"Expected build artifact not found in "
            f"{CONNECTOR_DIR / 'dist'}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Built: {exe_path}")
    return exe_path


if __name__ == "__main__":
    build()
