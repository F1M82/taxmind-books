# TaxMind Books — Local Development Setup

This guide gets a fresh checkout to a passing test suite in under 30
minutes. It assumes a developer machine with admin / sudo rights.

If anything in this document conflicts with the canonical architecture
docs, the architecture docs win — file a follow-up to fix this guide.

---

## 1. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Docker Desktop | latest | postgres + redis containers |
| Python | 3.11.x | backend + connector |
| Node.js | 20.x | mobile (Expo SDK 51) |
| Git | any modern | obvious |
| PowerShell 5+ / bash | — | the shell snippets below |

Backend and connector use `python -m venv` — no Poetry / Hatch global
install needed. Mobile uses npm (yarn / pnpm work but aren't tested
in CI).

---

## 2. Clone + start services

```bash
git clone https://github.com/F1M82/taxmind-books.git
cd taxmind-books
cp .env.example .env
docker compose up -d postgres redis
docker compose ps                 # both should be 'healthy'
```

`docker compose up -d` (no profile) brings up only Postgres + Redis.
The backend service is behind the `app` profile and isn't needed for
local dev (you run `uvicorn` against your venv).

The `taxmind_books` database is created automatically. For the test
suite a separate `taxmind_books_test` database is needed:

```bash
docker exec taxmind-postgres \
  psql -U taxmind -d taxmind_books \
  -c "CREATE DATABASE taxmind_books_test OWNER taxmind"
```

---

## 3. Backend

```bash
cd backend
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -e ".[dev]"
```

### 3.1 Apply migrations

```bash
$env:DATABASE_URL = "postgresql+psycopg://taxmind:taxmind@localhost:5432/taxmind_books"
alembic upgrade head
```

(Linux/mac: `export DATABASE_URL=postgresql+psycopg://...`).

### 3.2 Run the app

```bash
$env:REDIS_URL          = "redis://localhost:6379/0"
$env:JWT_SECRET         = "dev-jwt-secret-not-for-prod"
$env:SECRET_KEY         = "dev-secret-key-not-for-prod"
$env:CONNECTOR_JWT_SECRET = "dev-connector-secret-not-for-prod"
uvicorn app.main:app --reload --port 8000
```

Hit `http://localhost:8000/docs` for the OpenAPI explorer. The
"Try it out" form works against any endpoint you have credentials
for.

### 3.3 Run the tests

```bash
$env:DATABASE_URL              = "postgresql+psycopg://taxmind:taxmind@localhost:5432/taxmind_books_test"
$env:TEST_DATABASE_URL         = $env:DATABASE_URL
$env:TAXMIND_SKIP_TALLY_DISPATCH = "1"      # suppress the Celery dispatch in unit tests
python -m pytest -q
```

Coverage report: append `--cov=app --cov-report=term-missing`.
The CI gate is `--cov-fail-under=70`.

### 3.4 Common backend pitfalls

- **`InvalidTextRepresentation: invalid input syntax for type inet: "testclient"`**
  TestClient sets `request.client.host = "testclient"`. The audit
  emitter's `_coerce_ip()` strips that. If you're seeing this in a
  custom test app, route the `Request` through a helper that calls
  `_coerce_ip` before stashing `ip_address`.
- **`relation "connector_enrollment_codes" already exists`**
  The session-scoped `migrated_engine` fixture drops + recreates the
  schema once per session. A previous partial test run can leave
  state — drop the table manually and re-run.
- **Tests hang on Celery `.delay()`**
  `TAXMIND_SKIP_TALLY_DISPATCH=1` is set in `tests/conftest.py`;
  if you're running pytest from outside the repo's conftest, set it
  yourself.

---

## 4. Connector

```bash
cd connector
python -m venv .venv
.\.venv\Scripts\Activate.ps1            # or: source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                      # ~3s, 40 tests
```

To produce a Windows `.exe` locally (requires Windows + PyInstaller):

```powershell
pip install pyinstaller
python installer/build_exe.py            # → dist/TaxMindBooksConnector.exe
```

CI builds the `.exe` on every push to `main` via
`.github/workflows/connector-build.yml`.

---

## 5. Mobile

```bash
cd mobile
npm install --legacy-peer-deps
npx tsc --noEmit
npm test
```

Expo dev server:

```bash
npm run start
# scan the QR with Expo Go on a device, or hit `i` for iOS / `a` for Android
```

`app.json` `extra.API_BASE_URL` points at `http://localhost:8000` by
default. To target a deployed backend, edit `app.json` (or wire an
`app.config.js` per Expo's runtime-config pattern).

---

## 6. Running everything end-to-end

In separate terminals:

| Terminal | Command |
|---|---|
| postgres + redis | `docker compose up postgres redis` |
| backend | `uvicorn app.main:app --reload --port 8000` |
| celery worker | `celery -A app.workers.celery_app worker --loglevel info` |
| mobile | `cd mobile && npm run start` |

The Tally Desktop Connector only runs on Windows with TallyPrime open.
For local backend dev without Tally, the `/connector/status` endpoint
returns `connected: false` and the post-voucher dispatch quietly fails
into the retry queue. You can simulate a connector by running the
connector against the backend WS:

```bash
cd connector
$env:CONNECTOR_TOKEN       = "<paste the connector_token from /connector/enroll>"
$env:CONNECTOR_COMPANY_ID  = "<the company UUID>"
$env:BACKEND_WS_URL        = "ws://localhost:8000/api/v1/connector/ws"
python -m connector.main
```

The connector will register and the status endpoint flips to
`connected: true`.

---

## 7. Pre-commit hygiene

The same checks CI runs are runnable locally:

```bash
ruff check .                                     # whole-repo lint
python tools/lint/check_money_types.py backend/app
python tools/lint/check_audit_emit.py backend/app/services
python tools/lint/check_imports.py .
cd backend && python -m mypy app --ignore-missing-imports
```

Set up a `pre-commit` hook (out of scope for Phase 0) once these
stabilize.

---

## 8. Where to next

- For each Phase-0 task and its acceptance gate, see
  [`docs/PHASE_0_TASKS.md`](PHASE_0_TASKS.md).
- For the v1.2 amendments + Patches, see
  [`docs/AMENDMENTS_v1.2.md`](AMENDMENTS_v1.2.md).
- For the Phase-0 endpoint surface, see
  [`docs/API.md`](API.md) and the
  [`backend/tests/integration/openapi_phase0.yaml`](../backend/tests/integration/openapi_phase0.yaml)
  reference.
