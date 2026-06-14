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

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CONNECTOR_DIR = Path(__file__).resolve().parents[1]
INSTALLER_DIR = Path(__file__).resolve().parent

EXE_NAME = "TaxMindBooksConnector"
ENTRYPOINT = CONNECTOR_DIR / "connector" / "main.py"
ICON_PATH = INSTALLER_DIR / "icon.ico"
BUILD_INFO_PY = CONNECTOR_DIR / "connector" / "_build_info.py"
BUILD_INFO_JSON_NAME = "BUILD_INFO.json"


def _git(args: list[str]) -> str:
    """Run a git command in the connector dir; '' on any failure."""
    try:
        out = subprocess.run(
            ["git", *args],  # noqa: S607 - git resolved from PATH, by design
            cwd=CONNECTOR_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _capture_build_info() -> dict[str, object]:
    """Capture git provenance for this build. Best-effort; never raises."""
    sha = _git(["rev-parse", "--short=7", "HEAD"]) or "unknown"
    # Dirty == uncommitted changes under the connector package specifically.
    dirty = bool(_git(["status", "--porcelain", "--", "connector/connector"]))
    built_at = datetime.now(UTC).isoformat()
    return {"sha": sha, "dirty": dirty, "built_at": built_at}


def _write_build_info_module(info: dict[str, object]) -> None:
    """Write the gitignored module PyInstaller bundles into the .exe."""
    BUILD_INFO_PY.write_text(
        '"""Auto-generated at build time by installer/build_exe.py.\n'
        'Do not edit; gitignored; regenerated on every build."""\n'
        "from __future__ import annotations\n\n"
        f"BUILD_SHA = {info['sha']!r}\n"
        f"BUILD_DIRTY = {info['dirty']!r}\n"
        f"BUILT_AT = {info['built_at']!r}\n",
        encoding="utf-8",
    )


def _check_pyinstaller_available() -> None:
    try:
        import PyInstaller  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print(
            "PyInstaller is not installed. Run: pip install pyinstaller",
            file=sys.stderr,
        )
        sys.exit(1)


def build() -> Path:
    """Run PyInstaller. Returns the path to the built .exe."""
    _check_pyinstaller_available()

    # Stamp build provenance BEFORE PyInstaller so the generated module is
    # bundled into the frozen .exe (runtime reporting via the register
    # payload). The sidecar JSON is written after a successful build.
    info = _capture_build_info()
    _write_build_info_module(info)
    print(f"Build info: sha={info['sha']} dirty={info['dirty']} built_at={info['built_at']}")

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
    # Sidecar next to the artifact: lets dev_stack.ps1 read the build SHA
    # without launching the binary or holding a token (the pre-launch
    # staleness guard).
    sidecar = exe_path.parent / BUILD_INFO_JSON_NAME
    sidecar.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Built: {exe_path}")
    print(f"Sidecar: {sidecar}")
    return exe_path


if __name__ == "__main__":
    build()
