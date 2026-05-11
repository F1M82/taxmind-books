# TaxMind Books Connector — Installer

This directory holds the PyInstaller build script that produces a
single-file Windows `.exe` from `connector/connector/main.py`.

## Local build (Windows)

```powershell
cd connector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install pyinstaller
python installer/build_exe.py
```

The artifact lands at `connector/dist/TaxMindBooksConnector.exe`.

## CI build

`.github/workflows/connector-build.yml` builds on each push to `main`
and on tags `v*`. Outputs:

- Workflow artifact `connector-exe` (every build).
- GitHub Release asset (tag builds only).

## Icon

Drop a Windows `.ico` at `connector/installer/icon.ico` to brand the
executable. The build script picks it up automatically when present;
without it, PyInstaller uses the default Python icon. The icon is
intentionally git-ignored so design assets aren't versioned in this
code repo — store the source-of-truth in design tooling and copy in
at release time.
