# tools/

Developer tooling for TaxMind Books. Not shipped to production.

## dev_stack.ps1 — one-command dev-stack lifecycle

Brings the local stack to a known-good state (or reports / tears it down) with a
single command. Idempotent, fails loudly, pure orchestration (touches no
backend/connector/CI source). Design: `docs/dev_stack_script_design.md`.

```powershell
tools\dev_stack.ps1                 # same as -Action up
tools\dev_stack.ps1 -Action up      # Docker (pg+redis) -> backend (uvicorn, WMI-detached) -> connector (.exe, minimized)
tools\dev_stack.ps1 -Action status  # read-only report; exit 0 if all up, 1 otherwise
tools\dev_stack.ps1 -Action down    # stop connector + backend; leaves Docker + Tally running
tools\dev_stack.ps1 -Action up -Verify   # also logs in + checks GET /connector/status
```

Notes:
- **Windows PowerShell 5.1.** Run from anywhere; paths resolve relative to the repo.
- **Tally ODBC** can't be enabled by script — if `:9000` is down, `up` prints a
  WARN with the TallyPrime steps and continues. `status` counts Tally, so its
  exit is 1 until TallyPrime has ODBC enabled.
- **`-Verify`** reads credentials from env vars `DEV_TEST_USER` /
  `DEV_TEST_PASSWORD` (never hardcoded); if they're unset it fails loudly. The
  minted token is one-shot and not written to disk.
- **Connector `.exe`** is trusted as-is; `up` only prints its mtime as a
  staleness breadcrumb. Rebuild detection is a separate ticket
  (`memory/phase_0_5_connector_exe_versioning.md`) — rebuild manually with
  `connector\.venv\Scripts\python.exe installer\build_exe.py` if connector
  source changed.
- **Connector enrollment** is not automated; `up` trusts the `CONNECTOR_TOKEN`
  in `connector\dist\.env` and points you at the enroll ceremony (CLAUDE.md) if
  it's missing.
