# Project notes for Claude

## psql access

There is **no native PostgreSQL install** on this machine — `psql.exe` is not
on PATH and `C:\Program Files\PostgreSQL\` does not exist. The database runs
in Docker as container `taxmind-postgres` (image `postgres:16-alpine`,
published on `0.0.0.0:5432`).

To run `psql` against the project DB, exec into the container:

```powershell
docker exec -it taxmind-postgres psql -U taxmind -d taxmind_books
```

For one-shot queries (no `-it` so output is capturable):

```powershell
docker exec taxmind-postgres psql -U taxmind -d taxmind_books -c "SELECT 1;"
```

Credentials come from `backend/.env` (`DATABASE_URL`). The in-container
`psql` binary lives at `/usr/local/bin/psql` (PostgreSQL 16.13).

For Python-side queries that need ORM access, prefer the backend's
`SessionLocal`:

```powershell
cd "H:\Accounting Project\backend"
.venv/Scripts/python -c "from app.core.database import SessionLocal; from sqlalchemy import text; s=SessionLocal(); print(s.execute(text('SELECT 1')).scalar()); s.close()"
```

## Backend health endpoint

Liveness probe is `GET /health` (unprefixed, **not** `/api/v1/health`).
Returns `{"status":"ok","env":"<APP_ENV>"}`. Defined in
`backend/app/main.py`. `/api/v1/health/ready` is documented in
`docs/API.md` but is not implemented in Phase 0.

## Connector enrollment for local dev

The connector `.exe` (`connector/dist/TaxMindBooksConnector.exe`)
requires a `CONNECTOR_TOKEN` (1-year JWT issued by the backend) and a
`CONNECTOR_COMPANY_ID` before it will dial the backend WebSocket.
Tokens are not bundled in the build and are not persisted by default
between sessions — if the `.env` is missing, you must re-enroll.

**Two-step ceremony.**

1. **Issue a code.** As an `owner` of the target company (required
   role per `app/api/v1/connector.py:68`), call
   `POST /api/v1/connector/enrollment-codes` with the company's
   `X-Company-ID` header and an `Idempotency-Key`. Body `{}`. The
   response contains `code` (raw, single-use, 15-minute TTL) and
   `expires_at`.
2. **Exchange the code.** Anonymously call
   `POST /api/v1/connector/enroll` with `{"code": "<raw-code>"}`.
   Response contains `connector_id`, `company_id`, `connector_token`
   (JWT), and `expires_in_days` (365 by default).

**Connector config.**

Two configuration paths matter because the connector reads them
differently:

- Pydantic-loaded (`connector/connector/config.py`,
  `ConnectorSettings`): `CONNECTOR_TOKEN`, `BACKEND_WS_URL`,
  `TALLY_HOST`, `TALLY_PORT`, etc. These can live in
  `connector/dist/.env` next to the `.exe`.
- Direct `os.environ.get` lookup (`connector/connector/main.py:30`):
  **`CONNECTOR_COMPANY_ID`**. pydantic-settings does not propagate
  `.env` values into the process env, so this **must** be set in the
  actual environment when launching the `.exe`, not just in `.env`.

For local dev, set `BACKEND_WS_URL=ws://localhost:8000/api/v1/connector/ws`
(the packaged default points to the real production backend,
`wss://books.gcwealthguru.com/api/v1/connector/ws` — see
`connector/connector/config.py`).

**One-line local launch (PowerShell):**

```powershell
$env:CONNECTOR_TOKEN="<jwt>"; $env:CONNECTOR_COMPANY_ID="<company-uuid>"; `
$env:BACKEND_WS_URL="ws://localhost:8000/api/v1/connector/ws"; `
$env:TALLY_HOST="localhost"; $env:TALLY_PORT="9000"; `
& "H:\Accounting Project\connector\dist\TaxMindBooksConnector.exe"
```

**Launching from an agent session (no interactive desktop):** `Start-Process
-WindowStyle Minimized` can crash the PowerShell host itself when the
session has no interactive desktop (observed 2026-09-14) — the whole
`powershell.exe` call returns exit 1 with zero output, for *any*
`-WindowStyle Minimized` process, not just the connector. Symptom is
easy to misdiagnose as the connector crashing, since the .exe's own
logs never even get created (it dies before its logging setup runs).
Workaround: launch via the Bash tool in the background instead,
`CONNECTOR_COMPANY_ID=... run_in_background`, e.g.:

```bash
cd "H:\Accounting Project\connector\dist" && CONNECTOR_COMPANY_ID="<company-uuid>" ./TaxMindBooksConnector.exe
```
(with `run_in_background: true`). Verify with `tasklist | grep
TaxMindBooksConnector` (two processes = normal, PyInstaller bootstrap
+ real process) and `Get-NetTCPConnection -OwningProcess <pid>` to
confirm the established connection to the backend.

**Verification.** From any user that has membership in the company,
`GET /api/v1/connector/status` (auth + `X-Company-ID`) should flip
within a few seconds to `connected=true, tally_running=true,
connector_version=0.1.0`. If `connected=false` persists, check the
connector's stdout for missing-env errors or WS handshake failures.

## Production deployment (manual, no CI/CD)

There is no auto-deploy pipeline. The backend on the VPS
(`/opt/taxmind/app/backend/`) is a **plain file copy, not a git
clone** — there's no `.git` there, so `git pull` does nothing. To ship
a backend change:

1. `scp` the changed file(s) into the matching path under
   `/opt/taxmind/app/backend/` on the VPS.
2. `docker compose -f docker-compose.prod.yml build taxmind-api`
3. `docker compose -f docker-compose.prod.yml up -d --no-deps taxmind-api`
   (recreates only the API container — `postgres`/`redis` are
   untouched, `--no-deps` is required or compose will try to touch
   them too).
4. Verify `docker logs --tail 20 taxmind-prod-taxmind-api-1` (clean
   startup, WS clients reconnecting) and `GET /health` returns
   `{"status":"ok","env":"production"}`.

VPS host/port/SSH-key details are already recorded in this machine's
Claude memory (`vps_cohosting_recon.md`) — not duplicated here since
this file is checked into the repo.

## Tally company mapping: two representations that can drift

A company's "is this Tally-mapped" status is tracked in **two
places** that are not automatically kept in sync:

- `Company.tally_master_id` — the field
  `discovery_service.bind_discovery_reference`'s own collision check
  actually guards (raises `tally_mapping_collision` if it's already
  set to a different GUID).
- `ConnectorCompanyBinding` rows — what
  `GET /connector/{id}/tally-companies` uses to compute each
  discovery's `mapped_to_backend_company_id` (the "Unmapped" /
  "Mapped to..." badge in the mobile Tally Setup screen).

A company mapped via an older path (e.g. historical master-sync
reconciliation) can have `tally_master_id` set with **zero**
`ConnectorCompanyBinding` rows — confirmed live in production
2026-09-12 (Vighnaharta Agro Chemicals). `backend/app/api/v1/connector.py`
now folds `Company.tally_master_id` into the mapped-lookup too, so
don't reintroduce a binding-table-only lookup — it will silently
report an already-mapped company as "Unmapped" and invite a doomed
second mapping attempt.
