# Dev stack bring-up script — design

**Date:** 2026-06-14
**Status:** DESIGN ONLY — no script written this session.
**Ticket:** [[phase-0-5-env-bringup-scripting]] (Phase 0.5 hygiene, DECISION 8).
**Goal:** replace the week-long manual bring-up sequence with one idempotent,
loud-on-failure PowerShell command that takes the TaxMind Books dev stack to a
known-good state and reports what it did.

## Scope + guardrails (locked)

- **One script (or small set), pure orchestration.** No changes to backend,
  connector, or CI code. The script invokes existing artifacts
  (`docker-compose.yml`, `backend/logs/run_uvicorn.cmd`,
  `connector/dist/TaxMindBooksConnector.exe`) — it does not modify them.
- **No `.exe` rebuild detection.** That is the separate ticket
  [[phase-0-5-connector-exe-versioning]]. This script *trusts whatever `.exe`
  is on disk* and only prints its mtime as an informational breadcrumb (cheap,
  no rebuild logic).
- **No enrollment automation.** The connector's `CONNECTOR_TOKEN` is a 1-year
  JWT already on disk in `connector/dist/.env`; routine bring-up trusts it. If
  it is missing the script fails loudly and points at the enroll ceremony
  (CLAUDE.md §"Connector enrollment for local dev"). Automating the 15-minute
  enrollment-code ceremony is explicitly out of scope.
- **No Celery worker.** `run_uvicorn.cmd` sets `CELERY_TASK_ALWAYS_EAGER=1`, so
  voucher dispatch runs in-process on the API event loop (the BUG-003 mitigation,
  now retry-safe via the shipped connector idempotency cache). A separate worker
  is only needed for Celery-beat / lifecycle tasks, which are not part of the
  dev validation loop. Out of scope; a `-WithWorker` flag is noted as a future
  option, not built.

## Established facts the design rests on

- `docker compose up -d` (repo root, **no** profile) starts `postgres`
  (`taxmind-postgres`, 5432, healthcheck `pg_isready`) + `redis`
  (`taxmind-redis`, 6379, healthcheck `redis-cli ping`). The `backend` service
  is profile-gated (`app`) and intentionally unused.
- Backend runs from the venv via `backend/logs/run_uvicorn.cmd`, which `cd`s to
  `backend/` (so pydantic loads `backend/.env`), sets the eager/skip env, and
  serves `127.0.0.1:8000`, appending to `backend/logs/uvicorn.log`.
- Liveness: `GET http://127.0.0.1:8000/health` → `{"status":"ok","env":...}`
  (unprefixed; per CLAUDE.md).
- Connector launch (per [[connector-launch-pyinstaller-stdio]]): only
  `Start-Process -WindowStyle Minimized` reliably runs + logs; a healthy
  connector emits zero output — use `/api/v1/connector/status`, not the log, as
  truth. `CONNECTOR_COMPANY_ID` must be in the real process env (read via
  `os.environ`, not `.env`).
- Backend detach that survives the launching shell: WMI
  `Win32_Process.Create` (memory: PID survived tool-shell teardown where
  `Start-Process` job-object teardown killed it).

---

## Bring-UP inventory (per step)

Legend — Failure: **ABORT** (stop, non-zero exit), **WARN** (print, continue),
**RETRY** (poll with timeout then ABORT/WARN).

### Step 1 — Docker engine running
- **Pre-condition:** none.
- **Action:** `docker info` (probe). If it fails, start Docker Desktop
  (`Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"`) and
  poll `docker info` until success.
- **Verification:** `docker info` exit 0.
- **Failure:** RETRY (poll ~90s). If still down → ABORT (Docker is the
  foundation; nothing else can proceed).
- **Human intervention:** only if Docker Desktop install is broken.

### Step 2 — Postgres + Redis up and healthy
- **Pre-condition:** Step 1 passed.
- **Action:** `docker compose up -d` from repo root (idempotent — no-op if
  already running).
- **Verification:** poll
  `docker inspect -f '{{.State.Health.Status}}' taxmind-postgres` and
  `… taxmind-redis` until both report `healthy`.
- **Failure:** RETRY (poll ~60s, healthchecks are 5s interval / 10 retries).
  Still unhealthy → ABORT.
- **Human intervention:** none.

### Step 3 — Backend port free / stale uvicorn reconciled
- **Pre-condition:** Step 2 passed.
- **Action:** check `Get-NetTCPConnection -LocalPort 8000 -State Listen`. If a
  listener exists, probe `/health`; if it answers 200, treat as
  already-up (idempotent — skip Step 4). If the port is held but `/health`
  fails, that's a stale/broken process → kill its `OwningProcess`.
- **Verification:** port free OR an existing healthy backend identified.
- **Failure:** if a non-backend process holds 8000 → ABORT with the PID/name
  (don't blind-kill an unknown process).
- **Human intervention:** none (unless an unexpected process owns 8000).

### Step 4 — Launch backend (detached)
- **Pre-condition:** Step 3 left 8000 free.
- **Action:** WMI detach of the existing launcher:
  `Invoke-CimMethod Win32_Process -MethodName Create -Arguments
  @{ CommandLine = 'cmd /c ""H:\Accounting Project\backend\logs\run_uvicorn.cmd""' }`.
  Capture the returned `ProcessId`; write it to `backend/logs/uvicorn.pid`.
- **Verification:** Step 5 (`/health`) is the real success signal — the WMI PID
  is the `cmd` wrapper, not uvicorn itself.
- **Failure:** if `Win32_Process.Create` returns non-zero `ReturnValue` → ABORT.
- **Human intervention:** none.

### Step 5 — Backend healthy
- **Pre-condition:** Step 4 launched (or Step 3 found existing).
- **Action:** poll `Invoke-RestMethod http://127.0.0.1:8000/health`.
- **Verification:** HTTP 200 with `status=ok`.
- **Failure:** RETRY (poll ~30s). Still failing → ABORT and tail
  `backend/logs/uvicorn.log` into the script output (the loud-failure
  requirement — show why it didn't come up).
- **Human intervention:** none.

### Step 6 — Tally ODBC reachable (best-effort, human-gated)
- **Pre-condition:** none (independent of backend).
- **Action:** probe `TALLY_HOST:TALLY_PORT` (from `connector/dist/.env`,
  default `localhost:9000`) — a TCP connect, or a minimal
  `POST <ENVELOPE></ENVELOPE>` and check for a 200.
- **Verification:** port responds.
- **Failure:** **WARN, continue.** The script cannot enable Tally's HTTP/ODBC
  server (F12 → Advanced Config → Enable Tally HTTP Server → port 9000) — that
  is a manual TallyPrime UI action. Print exactly what's missing and the enable
  steps.
- **Human intervention:** YES — this is the one irreducible manual step. The
  script's job is to *detect and instruct*, not fix.

### Step 7 — Read connector credentials
- **Pre-condition:** none.
- **Action:** parse `connector/dist/.env` for `CONNECTOR_TOKEN`,
  `CONNECTOR_COMPANY_ID`, `BACKEND_WS_URL`, `TALLY_HOST`, `TALLY_PORT`.
- **Verification:** `CONNECTOR_TOKEN` and `CONNECTOR_COMPANY_ID` are non-empty.
- **Failure:** ABORT with a pointer to the enroll ceremony (token missing =
  not enrolled; no point launching the connector).
- **Human intervention:** only if re-enrollment is needed.

### Step 8 — Launch connector (detached, minimized)
- **Pre-condition:** Steps 5 + 7 passed (backend up so the connector has
  something to dial; creds present). Step 6 is *not* a hard pre-condition — the
  connector can run with Tally down and report `tally_running=false`.
- **Action:** set `$env:CONNECTOR_TOKEN/CONNECTOR_COMPANY_ID/BACKEND_WS_URL/
  TALLY_HOST/TALLY_PORT` in the script session, then
  `Start-Process -WindowStyle Minimized -FilePath
  connector\dist\TaxMindBooksConnector.exe` (child inherits the env at spawn —
  this is how `CONNECTOR_COMPANY_ID` reaches `os.environ`). Print the `.exe`
  mtime as a staleness breadcrumb (informational only).
- **Verification:** Step 9.
- **Failure:** if `Start-Process` throws → ABORT.
- **Human intervention:** none.

### Step 9 — Connector alive + (optional) registered
- **Pre-condition:** Step 8 launched.
- **Action (always):** after ~10s, confirm
  `Get-Process TaxMindBooksConnector` exists.
- **Action (opt-in `-Verify`):** log in as the dev test user to mint a fresh
  access token, then `GET /api/v1/connector/status` (auth +
  `X-Company-ID: CONNECTOR_COMPANY_ID`) and assert `connected=true`
  (and report `tally_running`).
- **Verification:** process alive (always); `connected=true` (if `-Verify`).
- **Failure:** process died → ABORT and surface that the connector exited
  (likely the PyInstaller stdio quirk if launched wrongly, or a bad token).
  `-Verify` not connected after ~15s → WARN (registration can lag; the log is
  silent by design).
- **Human intervention:** none.

---

## Bring-DOWN flow

- **Connector:** `Stop-Process -Name TaxMindBooksConnector` (graceful first,
  then force). It holds an open WS — must die before a clean re-up.
- **Backend:** resolve the listener on `127.0.0.1:8000`
  (`Get-NetTCPConnection -LocalPort 8000 → OwningProcess`) and `Stop-Process`
  it. Port-based resolution is robust regardless of how it was launched (the
  `uvicorn.pid` WMI PID is only the `cmd` wrapper). Clear `uvicorn.pid`.
- **Docker + Tally: left running** (established convention — postgres/redis are
  cheap and persistent; Tally is a UI app). A `-IncludeDocker` flag on `down`
  is noted as optional, not default.
- **Idempotent:** down is safe to run when nothing is up (no-op + report).

---

## Script interface + design-question answers

**Q1 — One script with verbs, or three scripts?**
**One script, verb-driven:** `tools/dev_stack.ps1 -Action up|down|status`
(default `up`). One file keeps the shared config (paths, ports, container
names, the `.env` location) DRY and in one place. `status` is a read-only
probe of all services (no launches) for a quick "what's running?" check.

**Q2 — Where does it live?**
`tools/dev_stack.ps1`. `tools/` already exists and holds dev tooling
(`tools/lint`, `tools/validation_report`); there is no `scripts/` dir.
Consistent with convention; no new top-level dir.

**Q3 — Output on success / failure / partial?**
One status line per service, prefixed `[OK]` / `[WARN]` / `[FAIL]` with the
detail (port, container health, PID, mtime). A final summary block. Exit codes:
`0` = all hard steps OK (Tally-down is a WARN, still `0` with a visible WARN);
non-zero = a hard ABORT occurred. On any ABORT, tail the relevant log
(`uvicorn.log`) into output so the failure is diagnosable without hunting.

**Q4 — Token refresh: script or manual?**
The **connector** needs no user token — it authenticates with its 1-year
`CONNECTOR_TOKEN`. A user access token is needed *only* for the opt-in
`-Verify` status check. So: `-Verify` mints a short-lived token by logging in
the dev test user; creds come from params/env (`-TestUser` / `-TestPassword`,
or env), **not hardcoded**, and default to skipping verification. The ~30-min
access-token churn the ticket mentions only bites the verification convenience,
not the stack itself. Routine `up` does not touch user tokens.

**Q5 — Verify `.exe` is current (rebuild check)?**
**No — deferred** to [[phase-0-5-connector-exe-versioning]] per guardrail. The
script prints the `.exe` mtime as an informational breadcrumb so staleness is
*visible* (the 2026-05-26 stale-binary trap), but performs no rebuild logic and
makes no rebuild decision. When the versioning ticket lands, this script calls
its staleness check; until then it trusts disk.

---

## Open questions surfaced (for the implementer / reviewer)

1. **`-Verify` test creds source.** Default-skip avoids embedding creds.
   Confirm the preferred non-default source: `-TestUser`/`-TestPassword` params,
   or env vars (e.g. `DEV_TEST_USER`/`DEV_TEST_PASSWORD`)? (Memory has the live
   dev creds, but a script should not hardcode them.) Recommendation:
   env vars, params override.
2. **Stale-but-listening backend on :8000.** Step 3 kills a process that holds
   8000 but fails `/health`. Confirm this auto-kill is acceptable, or should it
   WARN-and-ABORT to let the human decide? Recommendation: auto-kill only if the
   process image is `python`/`uvicorn`; ABORT for anything else.
3. **Connector already running.** If `TaxMindBooksConnector` is already up on
   `up`, restart it (to pick up a fresh `.env`/binary) or leave it? Recommend:
   leave it, print `[OK] connector already running (pid …, exe mtime …)`;
   restart only via `down` then `up`.
4. **PowerShell edition.** Target Windows PowerShell 5.1 (the environment
   default) — avoid `??`/ternary/`-AsHashtable`. Confirm no PS7 assumption.

## Effort

Design (this doc) done. Implementation: ~1 session for `up`/`down`/`status`
(backend + Docker + connector launch + health/PID verification), plus a couple
of smoke runs. Within the ticket's 1–2 session estimate. No code outside
`tools/dev_stack.ps1`.

*End of design. No script written, no commits, no services launched.*
