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
`backend/app/main.py`. `GET /api/v1/health/ready` (readiness: Postgres
`SELECT 1` + Redis `PING`; 200 `ready` / 503 `not_ready`, unauthenticated)
is implemented in `backend/app/api/v1/health.py` but **not yet deployed
to prod** (commit it, then use the manual deploy recipe below).

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

**Neither step requires curl/PowerShell anymore (2026-09-16).** Step 1:
an `owner` opens the mobile app → Dashboard → ADMIN → "Add a device" →
taps "Generate a code" (`AddDeviceScreen`, calls the same
`POST /connector/enrollment-codes`, no backend change needed since the
endpoint already existed). Step 2: on the new PC, just double-click the
`.exe` with no `.env` present — it detects the missing token, prompts
`Enrollment code:` right in the console window (3 attempts before
giving up), and writes `connector/dist/.env` itself on success,
merging with whatever's already there rather than overwriting it. Only
works when stdin is a real terminal (a double-clicked `.exe`, or run
directly in a visible PowerShell window) — a `-WindowStyle Minimized`
or otherwise non-interactive launch skips the prompt and fails exactly
as before, since there's nothing to answer it. See
`connector/connector/enrollment.py` (step 2) and
`mobile/src/screens/admin/AddDeviceScreen.tsx` (step 1). Verified
on-device 2026-09-19 (EAS build versionCode 9): owner generated a code
in the app, pasted it into the connector, `.env` written, connected.
Pitfalls hit that day:
- The app on a phone only has "Add a device" if it was built after
  commit `2ac3192`; an older APK simply lacks the screen. Rebuild via
  `eas build --platform android --profile development`.
- The `.exe` only has the enrollment prompt if built from `55f06aa` or
  later. A stale `dist/` exe prints `CONNECTOR_TOKEN missing` and exits.
- Piping a code in (`echo code | exe`, from an agent's Bash tool) does
  NOT work — the prompt checks for a real console. The user has to run
  it in their own PowerShell window. Also runs in the foreground:
  closing that window stops the connector (it was found dead overnight
  on 2026-09-19, which the app shows as "Tally connector disconnected").
- Manual no-mobile fallback: `docs/CONNECTOR_ENROLLMENT.md` (login →
  issue code → exchange, all API calls).

**Mobile talks to production, not the LAN.** EAS builds (both
`development` and `production` profiles in `mobile/eas.json`) bake
`EXPO_PUBLIC_API_BASE_URL=https://books.gcwealthguru.com`, which
`mobile/src/api/client.ts` reads before `app.json`'s
`extra.API_BASE_URL`. That `extra` value (a LAN IP) only matters for
Expo Go / dev-server runs — editing it does nothing for an installed
build. A connector pointed at `ws://localhost:8000` while the phone uses
production shows "disconnected": they are different backends.

**Connector config.**

All connector settings — `CONNECTOR_TOKEN`, `CONNECTOR_COMPANY_ID`,
`BACKEND_WS_URL`, `TALLY_HOST`, `TALLY_PORT`, etc. — are Pydantic-loaded
(`connector/connector/config.py`, `ConnectorSettings`) and can live in
`connector/dist/.env` next to the `.exe`; a process env var of the same
name overrides it, but isn't required. **Historical note:**
`CONNECTOR_COMPANY_ID` used to be read via a direct `os.environ.get` in
`main.py`, bypassing `.env` entirely — commit `aea1f10` ("route
operational config through Settings, not raw os.environ") fixed that.
If you're on a build older than that commit, the old caveat still
applies; verified fixed live 2026-09-16 against current `main`
(`.env`-only `CONNECTOR_COMPANY_ID`, no process env var, connector
loaded it and attempted a WS connection rather than exiting with
"CONNECTOR_COMPANY_ID missing").

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

**Migrations are a separate manual step (missing from the above until
2026-09-19).** The image only copies `app/`, not `alembic/`, and Postgres
has no host port, so `alembic` can't run in the live container or from
the host. Run it in a one-off container on the compose network with the
host `backend/` mounted, *before* step 2's restart:

```bash
cd /opt/taxmind/app && docker compose -f docker-compose.prod.yml run --rm --no-deps \
  -v /opt/taxmind/app/backend:/app --entrypoint alembic taxmind-api upgrade head
```

Check the current revision with
`docker exec taxmind-prod-postgres-1 psql -U taxmind -d taxmind_books -c "SELECT * FROM alembic_version;"`.
Before any migration: `cp -r` the backend dir and `pg_dump -Fc` (dump
into `/var/backups/taxmind/`). Prod is at `0020` as of 2026-09-19. When
several commits share one file (e.g. `models/company.py`), deploy them
together, not one at a time, or code and schema drift.

Auto-sync-on-connect (`5455d24`, `connector_ws.py` only) is on prod —
verified 2026-09-28: the VPS file is byte-identical to local and the
running container contains `_drive_auto_sync`. Safe over seeded data:
`upsert_from_sync` never overwrites `opening_balance`.

VPS host/port/SSH-key details are already recorded in this machine's
Claude memory (`vps_cohosting_recon.md`, `p32_opening_balance_seed.md`)
— not duplicated here since this file is checked into the repo. The
provider's noVNC console mangles multi-line paste; type one command at
a time, and never push file contents through it (scp instead).

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

**Who can map.** `POST /connector/tally-mapping` and the mobile "Connect
this company" button require **owner/admin** on a company the connector
serves (`_connector_authorized`, `connector.py`). An `accountant` member
gets a 403 that the app renders as the generic "Could not connect this
company" (only `gstin_already_registered` and `tally_mapping_collision`
have their own messages). Fix is a role change in Members, not a bug.

## Opening-balance seed (P3.2)

Implements `docs/PHASE_3_OPENING_BALANCE_ARCHITECTURE.md` (the
historical-Tally-mirror opening-balance design, APPROVED 2026-08-14).
Built 2026-09-15 (migration `0019`,
`backend/app/services/tally/opening_balance_seed.py`,
`POST /api/v1/connector/opening-balance-seed/{company_id}`). **Run live
on Vighnaharta Agro Chemicals 2026-09-19:** anchor `2025-04-01`, 621
ledgers seeded (186 non-zero), DB Dr 72,82,340.65 / Cr 80,16,142.52 ==
Tally's own totals. Do not re-run; a re-run is a no-op.

**What it does.** Calls the connector's `get_trial_balance` command and
writes each matched ledger's `opening_balance`/`balance_type` **once**.
Owner-only, Idempotency-Key required, 202 + background persist — same
shape as `sync_masters`/`trigger_sync`. The 202 says nothing about the
outcome; read the server log line
`opening_balance_seed <task_id> persisted ...: {'seeded': N, ...}`.

**Tally quirks found on the first real run (2026-09-19):**
- TallyPrime rejects the bare `Export Data`/`Trial Balance` request with
  `<RESPONSE>Unknown Request</RESPONSE>` (HTTP 200) for every date form.
  The connector used to read that as zero ledgers and report success, so
  the first run "succeeded" with `seeded: 0`. The connector now uses a
  TDL Ledger collection (`get_ledger_opening_balances`) and raises on a
  `<RESPONSE>` error. Fake-Tally unit tests had hidden this.
- Tally signs **debits negative**; the backend is positive = Dr. The
  connector negates. Any new Tally-balance reader must too.
- Ledger `OPENINGBALANCE` is the balance at the company's books-start
  only, so the handler refuses unless `to_date + 1 day` equals Tally's
  `STARTINGFROM` (`opening_balance_anchor_not_books_start`). A company
  whose books begin on the anchor has no earlier data for a "TB as of
  anchor − 1" (the original design), which is why this differs from the
  architecture doc's wording.
- Opening **stock** lives in stock items, not ledgers, so the seed does
  not carry it: Vighnaharta's opening TB was short by exactly its stock
  value (36 of 210 stock items non-zero, Dr 7,33,801.87, confirmed by a
  live Tally probe 2026-09-28). **Handled 2026-09-28 as a one-off on
  prod:** a books-only ledger "Opening Stock" (group `Stock-in-hand`, Dr
  7,33,801.87, id `e7e88843-0540-46d0-b232-b87b1cdfa730`, no
  `tally_master_id`, `opening_balance_seeded_at` stamped) was created via
  `LedgerService.create`; DB Dr and Cr now both 80,16,142.52. Pre-change
  dump: `/var/backups/taxmind/pre_opening_stock_20260928.dump`. It is NOT
  a general fix: the ledger is a static figure, not an inventory model,
  so P&L / closing stock are still not computed, and any other company
  needs the same manual step (or a proper seed step). Auto-sync leaves
  it alone (never deactivates ledgers absent from Tally).
- The company anchor locks even when zero ledgers were seeded (the
  no-op first run locked it to 2025-04-01), so pick the anchor carefully.

**Idempotency/safety invariants worth knowing before touching this
code again:**
- `Company.opening_balance_anchor_date` locks on the first successful
  run; a later call with a *different* anchor raises `409
  opening_balance_anchor_mismatch` rather than silently re-anchoring
  (re-anchoring would double-count in every FY's trial balance — see
  the architecture doc's "why a single field is correct").
- `Ledger.opening_balance_seeded_at` is the per-ledger idempotency
  marker: non-NULL means "the seed already wrote this one," and a
  same-anchor re-run is a no-op for it (protects against a later Tally
  reply reporting a different number).
- A ledger whose `opening_balance` is already non-zero but
  `opening_balance_seeded_at` is still NULL (e.g. a Phase A
  direct-entry value) is **never overwritten** — it's reported back as
  a conflict for operator review instead.
- Fails closed on company identity through the same
  `require_safe_company_mapping` gate `sync_masters` uses — the Trial
  Balance reply's Tally company GUID must match `Company.tally_master_id`,
  or nothing is written.

Master sync never carries opening balances (see
`[[pilot_phase_a_direct_entry]]` memory), so "ledgers visible, balances
all zero" after a fresh company sync means the seed hasn't been run,
not that sync is broken.

## Company-timezone-aware "today" (dashboard/reports)

Fixed 2026-09-15, deployed to production 2026-09-19 (migration `0020`, `companies.timezone`,
`app/core/company_time.py::company_today`). Every day-boundary default
— dashboard's `today`/`this_month`, `GET /reports/*`'s `as_of_date`/
`from_date`/`to_date` defaults — now goes through `company_today(company)`
instead of `datetime.now(UTC).date()` or the server-local `date.today()`.
Previously, for ~5.5h every night (00:00–05:29 IST), both of those UTC-
anchored calls labeled the *prior* IST day's data as "today" — wrong for
every India user (see `release/TAXMIND-PILOT-READINESS-2026-08-12.md`
§18/§20).

`companies.timezone` defaults to `'Asia/Kolkata'` for every company
(TaxMind Books is India-only; not yet exposed as an operator-settable
field — add that only if a non-India customer ever needs it). **If you
add a new endpoint that defaults a date to "today" for a company, call
`company_today(company)` — never `date.today()` or
`datetime.now(UTC).date()` directly, or you'll reintroduce this bug.**

## Rebuilding the connector wipes `connector/dist/.env`

`connector/installer/build_exe.py` does `shutil.rmtree("dist")` before
every build — **this deletes `connector/dist/.env`**, including
whatever `CONNECTOR_TOKEN` is sitting there (production or local dev).
Learned the hard way 2026-09-15: rebuilt the connector with a live
production token in `.env` and had to walk the user through
re-enrolling production from scratch. **Before running
`installer/build_exe.py` (or `tools/dev_stack.ps1`'s rebuild
suggestion), copy `connector/dist/.env` somewhere safe first** if it
has a token you care about — there is no other copy of a connector
token anywhere (never printed to logs, never committed, only ever
lived in that one file). Re-enrolling is always possible (CONNECTOR
tokens are cheap to reissue, see "Connector enrollment for local dev"
above) but requires an `owner`'s login, so it's not something to
trigger by accident. Practice since 2026-09-19: `cp dist/.env
.env.prod.bak` (outside `dist/`, untracked, holds a live token — delete
when done), stop the running exe (`taskkill //IM
TaxMindBooksConnector.exe //F`), build, copy the `.env` back, relaunch.

**`BUILD_INFO.json` can lie about `dirty`.** A build made from
uncommitted connector changes on 2026-09-19 was stamped
`sha=2ac3192 dirty=False`, so the staleness check would call it current
while it contained different code. Commit *before* building when the
build is meant to ship; the stamped sha then identifies the binary.

## Auto-sync-on-connect (2026-09-15)

The backend now auto-fires `sync_masters` — no manual
`POST /connector/sync/{company_id}` call needed — on two triggers:
every connector `register` (connect/reconnect) and every
`tally_company_changed` event (operator switched the open company in
Tally). See `app/api/v1/connector_ws.py::_drive_auto_sync` +
`docs/CONNECTOR_PROTOCOL.md` §"Auto-sync-on-connect". It resolves the
Tally company currently open (via `get_active_tally_company`), matches
it to whichever of the connector's *authorized* companies has that
`tally_master_id`, and only then syncs — never syncs a company the
connector isn't bound to, even if that company happens to be mapped to
the same Tally GUID. Runs fire-and-forget (`asyncio.create_task`,
never awaited inline from the register/event handler — the WS receive
loop that dispatches those handlers is the same loop that has to
receive the resulting `command_result`, so awaiting inline would
deadlock the connection). Gated by `TAXMIND_SKIP_TALLY_DISPATCH` even
though it's a read, not a dispatch — reuses the existing "don't reach
out to a connector during tests" switch rather than adding a second
flag; `tests/conftest.py` already sets it for the whole suite.

## Phase B additions, built 2026-09-28 (NOT yet deployed)

Local commits only until pushed/deployed. Deploy = the manual recipe above, **plus
migration `0021`** (`stock_valuations`; run it in the one-off container *before* the
restart), the backend files, a **rebuilt connector `.exe`** (back up `dist/.env`
first) and a new EAS build for the mobile screens.

**Tally gateway period rule (verified live).** The XML gateway is scoped to the
*company-level period set at the Gateway of Tally main menu (F2 there)* — NOT a period
changed inside an open report. A window outside it silently returns nothing, and
`ClosingBalance`/stock values are for that period. The connector now checks
(`get_active_period`, `TallyPeriodNotCovered` -> `tally_period_not_covered`).
**Never send `SVFROMDATE`/`SVTODATE` static variables to this Tally**: with valuation
collections they froze TallyPrime.

**Historical voucher import** — `POST /connector/voucher-import/{company_id}`
(owner, Idempotency-Key, **dry-run by default**) + `GET .../voucher-import/{task_id}`.
Monthly windows via `export_vouchers`; refuses unless the opening balances are seeded and
`from_date >= opening_balance_anchor_date`; re-checks the open Tally company (fail-closed
mapping gate) before every window; commits per window; stops on first failure. Idempotent
on `(company_id, tally_guid)`. Run state is in-process (lost on restart). Do a dry run
first; the operator must set Tally's period to cover the range. Code:
`services/tally/voucher_import_run.py`. Not live-verified end-to-end against prod.

**Report periods** — `GET /reports/periods` (years from the anchor/earliest voucher to
now) + mobile financial-year chips on P&L / Trial Balance / Balance Sheet.

**Balance sheet multi-year fix** — it used to 500 (`balance_sheet_unbalanced`) for any
date after the first financial year (prior years' profit was held nowhere). Now equity =
`prior_periods_profit_loss` + current period, like Tally. Importing Vighnaharta's FY
2026-27 vouchers would have triggered it.

**Stock valuation** — per financial year, Tally's own opening/closing stock value
(`stock_valuations`; `GET/PUT /stock-valuations`, `POST /connector/stock-valuation/{id}`,
connector command `get_stock_valuation`, mobile Dashboard -> "Stock valuation"). Applied
to P&L / balance sheet / dashboard only for whole-year windows with the needed rows;
without them everything is unchanged. Vighnaharta has NO valuation recorded yet: after
deploying, per year: set Tally's period to that one FY at the main menu, then "Read stock
from Tally" (first year's opening must equal the seeded `Opening Stock` ledger,
Dr 7,33,801.87). Tally shows **negative stock** for Vighnaharta (closing is a net credit;
dozens of items have negative closing quantity) — mirrored faithfully, flagged in the app;
the bookkeeper should review it. Design + as-built: `docs/PHASE_3_CLOSING_STOCK_DESIGN.md`.
