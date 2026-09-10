# TaxMind Books — Project Handover

_Snapshot for handing the project to another developer/AI. Written 2026-07-28._

**Superseded-since note (added 2026-09-10, non-editing):** this snapshot predates
P3.8 (multi-Tally-company support) and the production VPS deployment. §7's
"take it online" next-action is **done** — the backend is live at
`https://books.gcwealthguru.com` — and §6 item 8's connector-default claim is
now stale (see the inline correction there). For current status, read
`release/TAXMIND-PILOT-READINESS-2026-08-12.md` (and its addenda) over this
file. This document is kept as-is below for its still-accurate day-to-day
mechanics (local run commands, enrollment ceremony, repo layout).

---

## 1. What this is

**TaxMind Books** is an accounting product for Indian CMAs / MSMEs. It lets a
practitioner record accounting entries (vouchers) and view reports from a
**mobile app**, while the actual books live in **TallyPrime** (desktop
accounting software) on the client's own PC. A **connector** bridges the two:
it runs next to Tally and mirrors entries into it.

Three moving parts:

| Part | Tech | Where it runs |
|---|---|---|
| **Backend** | FastAPI (Python), Postgres, Redis | server (local now; cloud is the goal) |
| **Connector** | Python (packaged as a Windows `.exe`) | **the Windows PC that hosts TallyPrime** |
| **Mobile app** | Expo / React Native (TypeScript), SDK 51 | phone (Android dev-build APK) |

### The one constraint that governs everything

**TallyPrime only listens on `localhost:9000` on the client's PC.** So the
**connector must run on that PC** — it cannot move to the cloud. When the
backend goes online, the connector stays local and dials the public backend
over the internet (`wss://…`). Tally + connector are always on-prem; only the
backend + app talk to the cloud.

```
LOCAL today:   phone ──LAN──> backend(localhost:8000) <──LAN── connector ──> Tally(localhost:9000)
ONLINE goal:   phone ──https──> backend(cloud) <──wss── connector(on Tally PC) ──> Tally(localhost:9000)
```

---

## 2. Repository

- **Git remote:** `https://github.com/F1M82/taxmind-books.git`
- **Default branch:** `main` (convention: **commit/push directly to `main`**; PRs only for a surfaced reason)
- **CI:** GitHub Actions — jobs: `lint`, `type-check` (mypy backend, non-blocking), `backend` (pytest unit+integration+tenant-isolation+alembic round-trip, on a real Postgres), `connector` (mypy blocking + pytest), `mobile` (tsc + jest). Currently **green**.

### Layout

```
backend/     FastAPI app, Alembic migrations, tests, .venv
connector/   Python connector + PyInstaller build, own .venv, dist/*.exe
mobile/      Expo/React Native app (TypeScript)
docs/        Architecture, API, schema, protocol, validation, this file
validation/  Phase-0 validation logs + artifacts
tools/       scripts
salvage/     archived earlier code
```

---

## 3. Tech + conventions

- **Backend:** FastAPI, SQLAlchemy, Alembic, Pydantic v2. API under `/api/v1`.
  Liveness: `GET /health` (unprefixed) → `{"status":"ok","env":...}`.
- **Auth:** JWT. `POST /api/v1/auth/{register,login,refresh,me}`. Login is
  OAuth2 password flow (form-encoded `username`+`password`).
- **Tenancy:** every company-scoped call needs an `X-Company-ID` header. Roles:
  `owner` / `admin` / `accountant` / `viewer`. **Non-members get 404, not 403**
  (deny-by-hiding, intentional — `backend/app/api/deps.py`).
- **Writes need an `Idempotency-Key` header** (voucher create, sync, enrollment codes…).
- **Money** is a 2-decimal string on the wire (`"1234.56"`). `Money` type is
  `>= 0`; use **`SignedMoney`** where negatives are valid (balance-sheet lines,
  dashboard flows) — plain `Money` 500s on a negative.
- **Audit log is append-only** (DB trigger rejects UPDATE/DELETE).
- **Emails:** the validator rejects reserved TLDs like `.test`. Use real-looking
  domains (`example.com`, `taxmindbooks.dev`).

### Key endpoints

```
Auth        POST /api/v1/auth/register | login | refresh    GET /auth/me
Companies   POST/GET /api/v1/companies/                      GET/PATCH /companies/{id}
Members     POST/GET /companies/{id}/members                 PATCH/DELETE /companies/{id}/members/{user_id}
Ledgers     POST/GET /api/v1/ledgers/                        GET/PATCH/DELETE /ledgers/{id}
Vouchers    POST/GET /api/v1/vouchers/  (?from&to&status&source&channel)   GET/PATCH /vouchers/{id}
Audit       GET /api/v1/audit-logs/  (owner/admin)  (?action&from&to&limit)
Reports     GET /api/v1/reports/{trial-balance,profit-loss,balance-sheet,outstanding}
Dashboard   GET /api/v1/dashboard/home                       GET /api/v1/dashboard/financials?from&to
Connector   POST /api/v1/connector/enrollment-codes | enroll   GET /connector/status   POST /connector/sync/{company_id}
            WS  /api/v1/connector/ws
```

---

## 4. Running it locally

**Postgres/Redis run in Docker** (no native Postgres on the dev machine).

```powershell
# DB (container: taxmind-postgres, postgres:16-alpine, 0.0.0.0:5432)
docker start taxmind-postgres            # or: docker compose up -d postgres redis
docker exec taxmind-postgres psql -U taxmind -d taxmind_books -c "SELECT 1;"

# Backend (bind 0.0.0.0 so a LAN phone can reach it)
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
.venv/Scripts/python -m alembic upgrade head    # migrations

# Mobile (Metro dev server; env drives the API base URL)
cd mobile
$env:EXPO_PUBLIC_API_BASE_URL="http://<LAN-IP>:8000"
npx expo start --dev-client --host lan
```

**Connector** (must be on the Tally PC; set env vars for real — `CONNECTOR_COMPANY_ID`
is read via `os.environ`, not `.env`):

```powershell
$env:CONNECTOR_TOKEN="<jwt from enroll>"; $env:CONNECTOR_COMPANY_ID="<company-uuid>"
$env:BACKEND_WS_URL="ws://localhost:8000/api/v1/connector/ws"    # prod: wss://<domain>/...
$env:TALLY_HOST="localhost"; $env:TALLY_PORT="9000"
cd connector; .venv/Scripts/python -m connector.main            # from source = reliable + current
```

**Connector enrollment ceremony** (two steps):
1. As an **owner**, `POST /api/v1/connector/enrollment-codes` (with `X-Company-ID` +
   `Idempotency-Key`, body `{}`) → single-use `code` (15-min TTL).
2. Anonymously `POST /api/v1/connector/enroll` `{"code":"<code>"}` → `connector_token`
   (1-year JWT) + `company_id`.

**Verify:** `GET /api/v1/connector/status` → `connected:true, tally_running:true`.

---

## 5. Current state

### Phase 0 — essentially complete
All manual validation gates pass (validated headless-live 2026-07-28):
- **§7.1 Money**, **§7.2 Tenant isolation** (404 deny-by-hiding), **§7.3 Audit
  append-only**, **§7.4 Idempotency**, **§7.5 Tally connector**, **§7.6
  end-to-end** (register→enroll→sync→post→**confirmed in TallyPrime**→audit).
- **Reports** all pass (trial-balance, P&L, balance-sheet, outstanding).
- **Remaining:** only the human **Section 8 PASS/FAIL sign-off** in
  `docs/VALIDATION_REPORT.md` to formally close Phase 0.

### Features shipped this session (all committed, pushed, CI green)

| Commit | What |
|---|---|
| `4f9b4d5` | Fix: balance-sheet 500 on a negative contra-balance (`Money`→`SignedMoney`) + regression test |
| `94ef0a8` | `client_channel` — an `X-Client` header tags which client posted a voucher (mobile/web/api); `GET /vouchers/?channel=mobile` |
| `9b906cb` | Member-management API — list / change-role / remove (owner-gated, last-owner guard, audit) |
| `c3a36f7` | Mobile **Admin section** (role-gated): Members + Activity Log screens |
| `8a4616c` | Mobile dev-build env (eas.json) + `X-Client: mobile` header |
| `226f3a1` | **Dashboard financials** endpoint — Sales/Purchase/Expenses/Net-Profit, date-selectable, defaults to current Indian FY (Apr 1→today) |
| `c222e61` | Mobile **financials tile** with period selector (This FY default / Quarter / Month / Last FY / Custom) |

### Admin surface (no separate admin app)
Admin is a **role-gated section inside the same mobile app** — the ADMIN block
on the dashboard appears only for `owner`/`admin`. "Assign users" = add existing
users by email + role. "See logs" = the audit log. There is **no separate admin
login**; access is by role.

---

## 6. Critical constraints & gotchas

1. **Connector must be on the Tally PC** (Tally = `localhost:9000`). Non-negotiable.
2. **Backend registry is process-local** (see `memory` / BUG-003) → run **exactly
   one backend instance**. No horizontal scaling yet. Fine for launch.
3. **`CONNECTOR_COMPANY_ID`** must be a real environment variable at launch (read
   via `os.environ`), not just in `.env`.
4. **Tally Day Book XML export is stale-cached** — to confirm a specific voucher
   actually landed in Tally, query the **Voucher `<COLLECTION>`** (or Ledger
   Vouchers), NOT the Day Book export.
5. **Expo Go dropped SDK 51** — the app must run via an **EAS dev-build APK**
   (`eas build --profile development`), not store Expo Go.
6. **EAS free tier**: 15 Android builds/month, **low-priority queue** (can sit
   >1h before starting). Build config in `mobile/eas.json`.
7. Local-dev test user password (in tests/conftest + validation): `Hunter2-Validation!`.
8. Domains owned on Hostinger: **gcwealthguru.com / .in / .xyz**. As of this
   snapshot the connector's hardcoded default pointed at a placeholder
   (`wss://api.taxmindbooks.com/...`) on a domain that isn't owned — **this
   has since been fixed**; the connector now defaults to the real production
   backend, `wss://books.gcwealthguru.com/api/v1/connector/ws` (see
   `connector/connector/config.py`).

---

## 7. NEXT ACTION — take it online (the current ask)

Goal: make the app usable **without localhost**. Plan (see the constraint in §1):

### 7a. Backend → a VPS (NOT shared/WordPress hosting)
FastAPI + WebSockets + Postgres needs a real Linux box with Docker. Options:
Hostinger **VPS** (KVM, ~$5–8/mo, matches the existing `docker-compose.yml`), or
a PaaS (Render/Railway/Fly.io — managed Postgres + auto-TLS).

```
ssh VPS → install Docker → git clone → create production .env
→ docker compose --profile app up -d  (postgres + redis + backend)
→ alembic upgrade head
```

### 7b. HTTPS + WSS (required)
Front the backend with **Caddy** (auto Let's Encrypt TLS, ~5 lines). Gives
`https://api.gcwealthguru.com` and `wss://…/connector/ws`. Both the app and the
connector require TLS in production.

### 7c. DNS
One A record: `api.gcwealthguru.com → <VPS IP>` (Hostinger DNS).

### 7d. Production hardening (before public exposure)
The current config is dev-grade. Must change: strong `SECRET_KEY`/JWT secret,
real Postgres password, `APP_ENV=production`, CORS allowlist, drop Android
`usesCleartextTraffic` (unneeded over HTTPS). **Run a security review first.**

### 7e. Connector → point at prod
On the Tally PC: `BACKEND_WS_URL=wss://api.gcwealthguru.com/api/v1/connector/ws`,
re-enroll (fresh token against the cloud backend), set to auto-start.

### 7f. Mobile → production build
`EXPO_PUBLIC_API_BASE_URL=https://api.gcwealthguru.com`, then
`eas build --profile production` (add a `production` profile to `eas.json`);
distribute via Play Store internal testing or a direct APK.

### Open decisions to make before starting 7:
- **Where** to host the backend (Hostinger VPS vs PaaS).
- **Which domain/subdomain** (`api.gcwealthguru.com` recommended).

---

## 8. Also pending / parked
- **On-device test** of the complete dev APK (build `6c1f5a0c` — admin +
  financials + X-Client; was stuck in the EAS free queue).
- **v1.3 docs batch** — multi-Tally-company support (one CMA, many client
  companies on one Tally install). Fully designed in `docs/AMENDMENTS_v1.3.md`
  but **uncommitted/parked**; it's the planned "Phase 0.5". Not started in code.
- **Section 8 Phase-0 sign-off** (human).

## 9. Where to read more
`docs/ARCHITECTURE.md`, `docs/API.md`, `docs/SCHEMA.sql`,
`docs/CONNECTOR_PROTOCOL.md`, `docs/CONNECTOR_ENROLLMENT.md`,
`docs/EXTRACTION_CONTRACT.md`, `docs/VALIDATION_REPORT.md`,
`docs/AMENDMENTS_v1.3.md`, and the root `CLAUDE.md` (dev-environment notes).
