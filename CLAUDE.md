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

**Verification.** From any user that has membership in the company,
`GET /api/v1/connector/status` (auth + `X-Company-ID`) should flip
within a few seconds to `connected=true, tally_running=true,
connector_version=0.1.0`. If `connected=false` persists, check the
connector's stdout for missing-env errors or WS handshake failures.
