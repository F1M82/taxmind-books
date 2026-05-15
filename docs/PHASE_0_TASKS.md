# Phase 0 — Atomic Task List

**Status:** Frozen for execution. This is what Coder Claude executes against, in order.

Phase 0 ships a foundation: a working backend that authenticates users, manages companies and ledgers, posts vouchers, audits everything, isolates tenants, and connects to the Tally connector. No invoice scan, no SMS, no reconciliation. Those are Phase 1+.

## Phase 0 outcome (the human-visible deliverable)

A user can:
1. Register an account in the mobile app
2. Log in
3. Create a company
4. Install the Tally Desktop Connector and enroll it
5. See the connector status as "Connected" in the app
6. Create a ledger manually
7. Sync ledgers from Tally (one-shot import)
8. Create a voucher manually
9. See the voucher posted to TallyPrime within seconds
10. View an audit log of all actions

That's it. Phase 0 has no AI, no OCR, no automation. It is the spine on which Phase 1 will hang the wedge feature.

Phase 0 estimated effort: 80–120 hours of coding + 20 hours of validation. Solo, this is 3–4 weeks of focused work.

## Task numbering

Tasks are numbered `P0.NN`. Each task is atomic per the constitution Section 2: single responsibility, clear input/output, minimal files, independently testable.

Dependencies are explicit. A task can only start when all its dependencies are complete.

## Task list

### Foundation

#### P0.01 — Repository bootstrap

**Objective:** Create the initial repository structure per `REPO_LAYOUT.md`.

**Files:**
- `README.md` (project description, setup instructions)
- `.gitignore` (Python, Node, IDE, OS, secrets)
- `.env.example` (all env vars listed in `config.py` with placeholder values)
- `pyproject.toml` (root: ruff config, mypy config)
- `docker-compose.yml` (postgres, redis, backend service for local dev)
- `LICENSE` (proprietary placeholder)
- `.github/workflows/ci.yml` (skeleton; tasks fill it out)

**Dependencies:** None.

**Acceptance:**
- `git clone` + `docker-compose up` brings up postgres and redis healthy
- Empty backend image builds (no app code yet)
- `.env.example` enumerates: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `SECRET_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `S3_*`, `TALLY_HOST`, `TALLY_PORT`, `CONNECTOR_PORT`, `CONNECTOR_JWT_SECRET`, `WEB_URL`, `MOBILE_URL`

**Blast radius:** Repository root only. Cannot affect anything else.

#### P0.02 — Backend skeleton + config + database

**Objective:** FastAPI app factory, settings via pydantic-settings, SQLAlchemy declarative base, get_db dependency.

**Files:**
- `backend/pyproject.toml` (Python deps: fastapi, sqlalchemy, alembic, psycopg, pydantic-settings, etc.)
- `backend/app/__init__.py`
- `backend/app/main.py` (`create_app()` factory; `app = create_app()`)
- `backend/app/config.py` (Settings class)
- `backend/app/core/__init__.py`
- `backend/app/core/database.py` (engine, SessionLocal, get_db, Base)
- `backend/app/core/logging.py` (structured JSON logging config)
- `backend/Dockerfile`

**Dependencies:** P0.01.

**Acceptance:**
- `uvicorn app.main:app` starts cleanly
- `GET /` returns `{"status": "running"}` (placeholder)
- `GET /health` returns 200
- Settings load from env vars; missing required vars fail loudly
- Logger emits JSON-formatted lines

**Tests:**
- `tests/unit/test_config.py`: missing JWT_SECRET raises clear error
- `tests/integration/test_health.py`: GET /health returns 200

**Blast radius:** Backend skeleton only.

#### P0.03 — Alembic migrations setup

**Objective:** Alembic configured to manage migrations against the schema in `docs/SCHEMA.sql`.

**Files:**
- `backend/alembic.ini`
- `backend/alembic/env.py` (imports all models for autogenerate)
- `backend/alembic/script.py.mako`
- `backend/alembic/versions/` (empty initially)

**Dependencies:** P0.02.

**Acceptance:**
- `alembic upgrade head` works against an empty database (creates no tables yet — there are no migrations)
- `alembic check` passes
- `env.py` is wired such that future `alembic revision --autogenerate` will pick up models from `app/models/`

**Tests:**
- `tests/integration/test_alembic_roundtrip.py`: upgrade/downgrade round-trip works (after first migration is generated, in P0.05)

**Blast radius:** Migrations only.

#### P0.04 — Money handling primitives

**Objective:** Implement the Decimal-only money discipline per `MONEY.md`.

**Files:**
- `backend/app/core/money.py` (`MoneyColumn`, `money_column()`, `configure_decimal_context()`)
- `backend/app/schemas/__init__.py`
- `backend/app/schemas/common.py` (`Money`, `SignedMoney`, `TaxMindBooksBase`)
- `tools/lint/check_money_types.py` (AST-based check)

**Dependencies:** P0.02.

**Acceptance:**
- `app.main` calls `configure_decimal_context()` on startup
- A test schema using `Money` rejects float inputs (422)
- A test schema serializes Decimal as string in JSON
- `check_money_types.py` flags a deliberate float annotation in a test fixture
- `check_money_types.py` runs in CI

**Tests:**
- `tests/unit/core/test_money.py` — full money contract test per `MONEY.md`
- `tests/unit/lint/test_check_money_types.py` — lint script catches violations

**Blast radius:** core/money.py + schemas/common.py only.

### Models and migrations

#### P0.05 — Models: User, Company, UserCompany

**Objective:** SQLAlchemy models for user, company, user_companies. Generate first migration.

**Files:**
- `backend/app/models/__init__.py` (re-exports all models)
- `backend/app/models/base.py` (`Base`, `TenantScopedMixin`)
- `backend/app/models/user.py`
- `backend/app/models/company.py` (Company + UserCompany)
- `backend/alembic/versions/0001_initial_users_companies.py`

**Dependencies:** P0.03, P0.04.

**Acceptance:**
- All columns, constraints, indexes match `SCHEMA.sql` for these 3 tables
- Migration applies cleanly to empty DB
- Migration reverses cleanly
- Constraint violations behave as expected (uniqueness on email; CHECK on email format; etc.)

**Tests:**
- `tests/unit/models/test_user.py` — model instantiation, defaults, constraints
- `tests/unit/models/test_company.py`
- `tests/integration/test_alembic_roundtrip.py` now passes

**Blast radius:** 3 tables, 1 migration.

#### P0.06 — Models: Ledger

**Objective:** Add ledgers table and migration.

**Files:**
- `backend/app/models/ledger.py`
- `backend/alembic/versions/0002_ledgers.py`

**Dependencies:** P0.05.

**Acceptance:** matches `SCHEMA.sql` for ledgers table including the `pg_trgm` index.

**Tests:**
- `tests/unit/models/test_ledger.py`
- Roundtrip migration test passes

#### P0.07 — Models: Voucher, LedgerEntry

**Objective:** Vouchers and ledger entries tables.

**Files:**
- `backend/app/models/voucher.py` (Voucher + LedgerEntry classes)
- `backend/alembic/versions/0003_vouchers.py`

**Dependencies:** P0.06.

**Acceptance:** matches schema. DEFERRABLE constraint on voucher_number. CHECK constraints on amounts and confidence.

**Tests:**
- `tests/unit/models/test_voucher.py`
- Roundtrip works

#### P0.08 — Models: AuditLog + idempotency_keys

**Objective:** Audit log table with append-only triggers; idempotency_keys table.

**Files:**
- `backend/app/models/audit_log.py`
- `backend/app/models/idempotency_key.py`
- `backend/alembic/versions/0004_audit_idempotency.py` (includes the trigger SQL)

**Dependencies:** P0.07.

**Acceptance:**
- Tables match schema.
- The append-only trigger raises on UPDATE/DELETE attempts.
- An integration test issues `UPDATE audit_logs SET action='x'` and asserts the trigger fires.

**Tests:**
- `tests/unit/models/test_audit_log.py`
- `tests/integration/test_audit_log_append_only.py` — tries UPDATE, asserts exception

### Cross-cutting infrastructure

#### P0.09 — Auth: password hashing + JWT

**Objective:** Bcrypt password hashing, JWT creation/validation utilities.

**Files:**
- `backend/app/core/security.py` (`hash_password`, `verify_password`, `create_access_token`, `create_refresh_token`, `decode_token`)
- Settings: `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`

**Dependencies:** P0.02.

**Acceptance:**
- Password hashes use bcrypt with cost 12
- Access tokens contain `sub`, `exp`
- Refresh tokens are distinguishable from access tokens (`type` claim)
- Expired tokens fail to decode

**Tests:**
- `tests/unit/core/test_security.py` — full coverage of the four flows

#### P0.10 — Tenancy dependencies

**Objective:** Implement `get_current_user`, `get_active_company`, `require_role`, `get_scoped_session` per `TENANCY.md`.

**Files:**
- `backend/app/api/__init__.py`
- `backend/app/api/deps.py`

**Dependencies:** P0.09, P0.05.

**Acceptance:**
- `get_current_user` returns User from valid JWT, 401 from invalid
- `get_active_company` returns Company when user is a member, 404 otherwise
- `get_active_company` returns 404 (not 403) for non-membership
- `get_scoped_session` injects `WHERE company_id = ?` into queries on `TenantScopedMixin` models
- A query for a different company's voucher returns no rows even without explicit filter

**Tests:**
- `tests/unit/api/test_deps.py` — dependency unit tests
- `tests/integration/test_tenancy_dependencies.py` — full request path
- This is the foundation for the entire tenant_isolation/ tier; ensure it works thoroughly

#### P0.11 — Audit emitter

**Objective:** Implement `AuditContext`, `AuditEmitter`, `_normalize_for_json`, `_compute_diff`, `_ALLOWED_ACTIONS` per `AUDIT.md`.

**Files:**
- `backend/app/core/audit.py`
- `tools/lint/check_audit_emit.py` (AST check that flags missing emit calls)

**Dependencies:** P0.08, P0.10.

**Acceptance:**
- AuditEmitter writes a row with the right shape
- Decimal/UUID/Enum/datetime serialize correctly via `_normalize_for_json`
- Sensitive keys (`password`, `secret`, `token`, `api_key`) are redacted
- Unknown action raises ValueError
- `check_audit_emit.py` flags a deliberate "service mutates without emitting" fixture

**Tests:**
- `tests/unit/core/test_audit.py`
- `tests/unit/lint/test_check_audit_emit.py`

#### P0.12 — Idempotency handler

**Objective:** Implement `IdempotencyHandler` per `IDEMPOTENCY.md`.

**Files:**
- `backend/app/core/idempotency.py`
- Updates to `app/api/deps.py`: `get_idempotency_handler`

**Dependencies:** P0.08, P0.10.

**Acceptance:**
- Missing key on `required=True` raises 400
- First request stores response; second with same body returns stored response (200/201, not 409)
- Different body with same key returns 409 idempotency_replay
- Different path with same key returns 409 idempotency_key_misuse
- Stale lock (>60s, no completed_at) is taken over

**Tests:**
- `tests/unit/core/test_idempotency.py`
- `tests/integration/test_idempotency_e2e.py` — using a test endpoint

#### P0.13 — Error handling middleware

**Objective:** Standardized error envelope per `API.md`. Domain exception hierarchy.

**Files:**
- `backend/app/core/exceptions.py` (DomainException, NotFound, Conflict, ValidationFailed, Forbidden, etc.)
- `backend/app/api/errors.py` (FastAPI exception handlers)

**Dependencies:** P0.10.

**Acceptance:**
- Pydantic 422 errors emerge in the standard envelope
- Domain exceptions return the right HTTP status with code and message
- `request_id` echoes from header (or is generated)
- 500 errors don't leak stack traces

**Tests:**
- `tests/integration/test_error_envelope.py` — exercises each error type

### Auth endpoints

#### P0.14 — Auth: register

**Objective:** `POST /api/v1/auth/register` per `API.md`.

**Files:**
- `backend/app/api/v1/__init__.py`
- `backend/app/api/v1/router.py`
- `backend/app/api/v1/auth.py` (register only)
- `backend/app/schemas/auth.py`
- `backend/app/services/auth_service.py`

**Dependencies:** P0.09, P0.10, P0.13.

**Acceptance:** matches `API.md`. Email lowercased. Bcrypt hashing. Phone format validated.

**Tests:**
- `tests/integration/api/test_auth_register.py`
- Audit log written: `user.created`

#### P0.15 — Auth: login + refresh + me + password

**Objective:** Remaining auth endpoints.

**Files:** Updates to `auth.py`, `auth_service.py`, `schemas/auth.py`.

**Dependencies:** P0.14.

**Acceptance:** matches API.md. last_login_at updated on login. password change writes audit log `user.password_changed`.

**Tests:**
- `tests/integration/api/test_auth_login.py`
- `tests/integration/api/test_auth_refresh.py`
- `tests/integration/api/test_auth_me.py`
- `tests/integration/api/test_auth_password.py`

### Companies endpoints

#### P0.16 — Companies: CRUD + members

**Objective:** Companies and member management endpoints.

**Files:**
- `backend/app/api/v1/companies.py`
- `backend/app/schemas/company.py`
- `backend/app/services/company_service.py`

**Dependencies:** P0.15.

**Acceptance:** matches API.md. Creator becomes owner. Audit logs for each mutation.

**Tests:**
- `tests/integration/api/test_companies.py`
- `tests/tenant_isolation/test_companies_isolation.py` (the first isolation test)

### Ledgers endpoints

#### P0.17 — Ledgers: CRUD + fuzzy search

**Objective:** Ledger management endpoints.

**Files:**
- `backend/app/api/v1/ledgers.py`
- `backend/app/schemas/ledger.py`
- `backend/app/services/ledger_service.py`

**Dependencies:** P0.16.

**Acceptance:** matches API.md. Soft-delete only. `q` query uses pg_trgm.

**Tests:**
- `tests/integration/api/test_ledgers.py`
- `tests/tenant_isolation/test_ledgers_isolation.py`

### Vouchers endpoints

#### P0.18 — Vouchers: create

**Objective:** `POST /api/v1/vouchers/` with full validation, idempotency, audit.

**Files:**
- `backend/app/api/v1/vouchers.py`
- `backend/app/schemas/voucher.py`
- `backend/app/services/voucher_service.py`

**Dependencies:** P0.17, P0.11, P0.12.

**Acceptance:** matches API.md. Dr/Cr balance check. Ledger ownership check. GST split validation. Audit log `voucher.created`. Idempotency-Key required.

**Tests:**
- `tests/integration/api/test_vouchers_create.py`
- `tests/tenant_isolation/test_vouchers_isolation.py`

#### P0.19 — Vouchers: read, list, update, cancel

**Objective:** Remaining voucher endpoints per API.md.

**Files:** Updates to `vouchers.py`, `voucher_service.py`.

**Dependencies:** P0.18.

**Acceptance:** matches API.md. Immutable fields rejected on PATCH. Cancel writes audit log.

**Tests:**
- `tests/integration/api/test_vouchers_read.py`
- `tests/integration/api/test_vouchers_update.py`
- `tests/integration/api/test_vouchers_cancel.py`

### Audit log read API

#### P0.20 — Audit logs: list

**Objective:** `GET /api/v1/audit-logs/` per API.md.

**Files:**
- `backend/app/api/v1/audit_logs.py`
- `backend/app/schemas/audit_log.py`

**Dependencies:** P0.16.

**Acceptance:** matches API.md. require_role('owner', 'admin'). Cursor pagination.

**Tests:**
- `tests/integration/api/test_audit_logs.py`
- `tests/tenant_isolation/test_audit_logs_isolation.py`

### Connector

#### P0.21 — Connector: salvage tally_client.py

**Objective:** Bring `tally_client.py` from Qwen-Tallyonmobile salvage into the new connector skeleton, cleaned up.

**Files:**
- `connector/pyproject.toml`
- `connector/connector/__init__.py`
- `connector/connector/tally_client.py` (cleaned up version of salvaged code)
- `connector/connector/config.py`
- `connector/tests/unit/test_tally_client.py`

**Dependencies:** None.

**Acceptance:**
- Methods: ping, get_ledger, get_all_ledgers, get_all_groups, post_voucher, get_trial_balance, get_outstanding (per `CONNECTOR_PROTOCOL.md` command catalog)
- Type-checked
- Tests with `pytest-httpx` fakes against canned XML responses

#### P0.22 — Connector: WS client

**Objective:** WebSocket client with reconnect backoff and heartbeat per CONNECTOR_PROTOCOL.md.

**Files:**
- `connector/connector/ws_client.py`
- `connector/connector/main.py` (entry point, reconnect loop)
- `connector/connector/message_handlers.py`

**Dependencies:** P0.21.

**Acceptance:**
- Connects with token + X-Company-ID
- Reconnect with exponential backoff
- Heartbeat every 30s
- Receives `command`, dispatches to message_handlers, sends `command_result`
- Verifies command's `company_id` matches its registered company

**Tests:**
- `connector/tests/integration/test_ws_lifecycle.py` (against a fake backend WS)

#### P0.23 — Connector: enrollment endpoint (backend) + token issuance

**Objective:** `POST /api/v1/connector/enroll` exchange one-time code for connector token. Connector token JWT.

**Files:**
- Updates to `app/api/v1/`: `connector.py` for enroll
- Settings: `CONNECTOR_JWT_SECRET`
- Updates to `app/core/security.py`: connector token helpers

**Dependencies:** P0.16.

**Acceptance:** matches CONNECTOR_PROTOCOL.md token format. One-time code expires in 15min.

**Tests:**
- `tests/integration/api/test_connector_enroll.py`

#### P0.24 — Connector: WS endpoint (backend) + active registry

**Objective:** `WS /api/v1/connector/ws` endpoint. Connection registry with command dispatch.

**Files:**
- `backend/app/api/v1/connector_ws.py`
- `backend/app/services/tally/connector_registry.py`

**Dependencies:** P0.23.

**Acceptance:** per CONNECTOR_PROTOCOL.md. Token validation, register/heartbeat, command dispatch with futures, close codes.

**Tests:**
- `tests/integration/test_connector_ws.py` (uses FastAPI WS test client)

#### P0.25 — Connector: status endpoint

**Objective:** `GET /api/v1/connector/status` returns current connection state.

**Files:** Update `connector.py` route.

**Dependencies:** P0.24.

**Tests:**
- `tests/integration/api/test_connector_status.py`

#### P0.26 — Connector: sync_masters command + voucher_dispatcher

**Objective:** Implement `sync_masters` and `post_voucher` as backend commands; voucher dispatcher worker.

**Files:**
- `backend/app/services/tally/voucher_dispatcher.py`
- `backend/app/workers/celery_app.py`
- `backend/app/workers/posting_tasks.py`

**Dependencies:** P0.24, P0.18.

**Acceptance:**
- After voucher creation, dispatcher enqueues posting task
- Worker calls connector.send_command('post_voucher', ...)
- On success, voucher.tally_posted_at and audit log written
- On retryable error, Celery retry with backoff

**Tests:**
- `tests/integration/workers/test_posting_task.py` (with mocked connector)

#### P0.27 — Connector: sync trigger endpoint

**Objective:** `POST /api/v1/connector/sync/{company_id}` enqueues a sync_masters command.

**Files:** Update `connector.py`.

**Dependencies:** P0.26.

**Tests:**
- `tests/integration/api/test_connector_sync.py`

#### P0.28 — Connector: PyInstaller build

**Objective:** Build the connector as a Windows .exe.

**Files:**
- `connector/installer/build_exe.py`
- `connector/installer/icon.ico`
- `.github/workflows/connector-build.yml`

**Dependencies:** P0.27.

**Acceptance:** Building produces a single `.exe` runnable on Windows 10/11. CI builds on each push to main.

### Mobile (minimal)

#### P0.29 — Mobile: Expo bootstrap + auth screens

**Objective:** RN Expo project with login/register/dashboard skeleton.

**Files:**
- `mobile/package.json`, `mobile/tsconfig.json`, `mobile/app.json`
- `mobile/App.tsx` (providers)
- `mobile/src/api/client.ts`, `auth.ts`
- `mobile/src/context/AuthContext.tsx`
- `mobile/src/screens/auth/LoginScreen.tsx`, `RegisterScreen.tsx`
- `mobile/src/screens/dashboard/DashboardScreen.tsx`
- `mobile/src/navigation/RootNavigator.tsx`

**Dependencies:** P0.15.

**Acceptance:**
- `npm run start` launches Expo
- User can register, log in, see dashboard
- Token refresh on 401
- API base URL from `.env`

**Tests:**
- `mobile/tests/screens/Login.test.tsx`

#### P0.30 — Mobile: company switcher + connector status

**Objective:** Company list/create screens; connector-status display.

**Files:**
- `mobile/src/screens/companies/`
- `mobile/src/context/CompanyContext.tsx`
- `mobile/src/screens/dashboard/ConnectorStatusCard.tsx`

**Dependencies:** P0.29.

**Acceptance:** User can switch active company; X-Company-ID header set on every request; connector status visible.

**Tests:**
- `mobile/tests/screens/Companies.test.tsx`

#### P0.31 — Mobile: ledger list + manual voucher entry

**Objective:** Browse ledgers; create a manual voucher (form).

**Files:**
- `mobile/src/screens/ledgers/LedgerListScreen.tsx`
- `mobile/src/screens/vouchers/VoucherEntryScreen.tsx`, `VoucherListScreen.tsx`
- `mobile/src/utils/money.ts` (per MONEY.md)
- `mobile/src/api/vouchers.ts`, `ledgers.ts`

**Dependencies:** P0.30.

**Acceptance:** user can create a Receipt voucher with two entries; sees it in the list; sees its Tally posting status update.

**Tests:**
- `mobile/tests/screens/VoucherEntry.test.tsx`

### CI + Lint

#### P0.32 — CI: full pipeline

**Objective:** Bring the CI workflow to the full state per `TESTING.md`.

**Files:**
- `.github/workflows/ci.yml` (lint, type-check, unit, integration, tenant_isolation, migration round-trip, OpenAPI contract, coverage)

**Dependencies:** P0.04, P0.10, P0.11, P0.20.

**Acceptance:**
- All gate layers run
- Failure in any layer blocks merge
- CI runs in <6 minutes on a clean PR
- Coverage report generated and threshold enforced

**Tests:**
- (the CI workflow itself)

#### P0.33 — Lint: import boundaries + repo-layout

**Objective:** Implement `tools/lint/check_imports.py` per `REPO_LAYOUT.md` module-boundary rules.

**Files:**
- `tools/lint/check_imports.py`

**Dependencies:** P0.02.

**Acceptance:**
- Forbidden imports flagged (e.g., models importing services)
- Runs in CI

**Tests:**
- `tools/tests/test_check_imports.py`

#### P0.34 — OpenAPI contract test

**Objective:** Test that auto-generated OpenAPI matches `docs/API.md` for Phase 0 endpoints.

**Files:**
- `backend/tests/integration/test_openapi_contract.py`

**Dependencies:** P0.20, P0.27.

**Acceptance:** drift between code and `API.md` fails the test. The test's reference data lives next to it (a curated YAML extracted from API.md).

### Documentation refresh

#### P0.35 — README + setup guide

**Objective:** End-to-end README that walks a new developer (or you in 6 months) through setup.

**Files:**
- `README.md` (rewrite)
- `docs/SETUP.md` (detailed)

**Dependencies:** All previous P0 tasks.

**Acceptance:** A reader following the README clones the repo and gets the stack running in < 30 minutes.

---

## v1.2 — Additional Phase 0 tasks

The following tasks were added in v1.2 per the AMENDMENTS document. They sit alongside the original 35 tasks; sequencing notes below.

#### P0.36 — Manual voucher entry: all 8 voucher types

**Objective:** Expand P0.31 (mobile voucher entry) from Receipt-only to all 8 voucher types: Receipt, Payment, Sales, Purchase, Journal, Contra, Debit Note, Credit Note. Manual entries land in Tally as Regular vouchers (`as_optional=false`).

**Files:**
- Updates to `mobile/src/screens/vouchers/VoucherEntryScreen.tsx` (or split into per-type screens)
- New: type-specific validation rules in `backend/app/services/voucher_service.py`

**Dependencies:** P0.18, P0.31.

**Acceptance:** Each voucher type can be created on mobile with type-specific validation per `API.md` (e.g., Sales requires a Debtors-group ledger; Contra accepts only Bank/Cash group ledgers).

**Tests:**
- `tests/integration/api/test_vouchers_create_by_type.py` — eight test classes, one per voucher type, each covering happy path + validation rejections

#### P0.37 — Voucher Optional/Regular fields and rejection state

**Objective:** Add the v1.2 voucher columns and migration.

**Files:**
- Updates to `backend/app/models/voucher.py` — add `is_optional_in_tally`, `approved_to_regular_at`, `approved_to_regular_by`, `optional_rejection_reason`, `optional_rejected_at`, `optional_rejected_by`. Update `VoucherStatus` enum.
- New migration `backend/alembic/versions/0010_voucher_optional_fields.py`

**Dependencies:** P0.07.

**Acceptance:** Migration applies cleanly. Existing vouchers default to `is_optional_in_tally=false`.

**Tests:**
- Roundtrip migration test passes

#### P0.38 — Reports endpoints (trial balance, P&L, balance sheet, outstanding)

**Objective:** Implement the four Phase 0 reports per `REPORTS.md`.

**Files:**
- `backend/app/api/v1/reports.py`
- `backend/app/services/reporting/` directory:
  - `__init__.py`
  - `trial_balance.py`
  - `profit_loss.py`
  - `balance_sheet.py`
  - `outstanding.py`
  - `tally_groups.py` (group-classification lookup)
- `backend/app/schemas/reports.py`

**Dependencies:** P0.18, P0.19.

**Acceptance:**
- All four endpoints match `REPORTS.md` shape
- Universal rules R1–R9 enforced (Optional excluded, cancelled excluded, etc.)
- Tests exercise: balanced trial balance, P&L net = Income − Expense, Balance Sheet equation holds
- Performance: trial balance for 1000-voucher company < 500ms

**Tests:**
- `tests/integration/api/test_reports_trial_balance.py`
- `tests/integration/api/test_reports_profit_loss.py`
- `tests/integration/api/test_reports_balance_sheet.py`
- `tests/integration/api/test_reports_outstanding.py`
- `tests/golden/test_reports_tie_out_to_tally.py` — fixture-based, compares output to known-good Tally exports

#### P0.39 — Mobile reports screens

**Objective:** Mobile UI for the four Phase 0 reports.

**Files:**
- `mobile/src/screens/reports/TrialBalanceScreen.tsx`
- `mobile/src/screens/reports/ProfitLossScreen.tsx`
- `mobile/src/screens/reports/BalanceSheetScreen.tsx`
- `mobile/src/screens/reports/OutstandingScreen.tsx`
- `mobile/src/api/reports.ts`

**Dependencies:** P0.38.

**Acceptance:**
- User can view each report with date filters
- Numbers display via `formatINR()`
- Pull-to-refresh works
- Loading and error states handled

**Tests:**
- `mobile/tests/screens/reports/*.test.tsx`

#### P0.40 — Dashboard endpoint

**Objective:** Implement `GET /api/v1/dashboard/home` per `REPORTS.md` § Dashboard.

**Files:**
- `backend/app/api/v1/dashboard.py`
- `backend/app/services/dashboard_service.py`
- `backend/app/schemas/dashboard.py`

**Dependencies:** P0.25, P0.38.

**Acceptance:** Single response hydrating connector status, today's metrics, this-month metrics, outstanding totals, GST liability MTD, alerts. Performance budget 500ms p95.

**Tests:**
- `tests/integration/api/test_dashboard.py`

#### P0.41 — Mobile home dashboard screen

**Objective:** Replace placeholder DashboardScreen with the v1.2 dashboard.

**Files:**
- `mobile/src/screens/dashboard/DashboardScreen.tsx` (rewrite)
- `mobile/src/components/dashboard/` (tile components)

**Dependencies:** P0.40.

**Acceptance:** Tiles render, alerts surface, pull-to-refresh works, navigation to detail screens works.

**Tests:**
- `mobile/tests/screens/Dashboard.test.tsx`

#### P0.42 — Onboarding checklist endpoint

**Objective:** `GET /api/v1/onboarding/checklist` per API.md.

**Files:**
- `backend/app/api/v1/onboarding.py`
- `backend/app/services/onboarding_service.py`

**Dependencies:** P0.40.

**Acceptance:** Items reflect actual state (queries against existing tables; no separate state table).

**Tests:**
- `tests/integration/api/test_onboarding.py`

#### P0.43 — Mobile onboarding checklist screen

**Objective:** Onboarding card on dashboard + dedicated screen.

**Files:**
- `mobile/src/screens/onboarding/OnboardingScreen.tsx`
- `mobile/src/components/dashboard/OnboardingTile.tsx`

**Dependencies:** P0.42.

**Acceptance:** New users see the checklist; completed items persist; clicking an incomplete item navigates to the relevant flow.

#### P0.44 — Push notification: device registration + dispatch infrastructure

**Objective:** Backend infra for push notifications. Phase 0 ships the registration endpoint and the dispatcher; specific notification triggers ship in Phase 1.

**Files:**
- Updates to `backend/app/models/device_token.py`
- New migration `0011_device_tokens.py`
- `backend/app/api/v1/devices.py`
- `backend/app/services/notification_service.py`
- `backend/app/integrations/fcm_client.py`
- `backend/app/integrations/apns_client.py`

**Dependencies:** P0.15.

**Acceptance:**
- POST /devices/register stores the token
- DELETE /devices/{id} deactivates
- `notification_service.send_to_user(user_id, notification)` dispatches via FCM/APNs based on platform
- Audit log entries for register/unregister
- Idempotent re-registration of the same token (returns existing row, updates `last_active_at`)

**Tests:**
- `tests/integration/api/test_devices.py`
- `tests/integration/test_notification_dispatch.py` (with mocked FCM/APNs)

#### P0.45 — Account deletion request (DPDP Phase 0)

**Objective:** Endpoints and grace-period logic for DPDP-compliant account deletion.

**Files:**
- `backend/app/models/account_deletion_request.py`
- New migration `0012_account_deletion_requests.py`
- `backend/app/api/v1/account.py` (deletion-request endpoints only; data-export is Phase 1)
- `backend/app/services/account_lifecycle_service.py`
- `backend/app/workers/lifecycle_tasks.py` (Celery beat task — daily scan for expired grace periods)

**Dependencies:** P0.16.

**Acceptance:**
- POST /account/deletion-request creates a grace-period row, blocks if user is sole owner
- DELETE /account/deletion-request cancels during grace
- Daily Celery beat task: identifies grace-expired requests, processes hard-delete + PII scrub + audit log
- The hard-delete preserves audit logs of OTHER users in the same companies; only the deleted user's PII is scrubbed
- Email confirmation on request, cancellation, and completion

**Tests:**
- `tests/integration/api/test_account_deletion.py`
- `tests/integration/workers/test_account_deletion_task.py`

#### P0.46 — Connector: post_voucher with as_optional + approve/reject commands

**Objective:** Extend the connector salvage to support v1.2 Optional voucher commands.

**Files:**
- Updates to `connector/connector/tally_client.py`:
  - `post_voucher()` accepts `as_optional` parameter; emits `<ISOPTIONAL>Yes</ISOPTIONAL>` when set
  - New: `approve_optional_voucher(voucher_guid)` — alters voucher to set `<ISOPTIONAL>No</ISOPTIONAL>`
  - New: `reject_optional_voucher(voucher_guid)` — deletes voucher
- Updates to `connector/connector/message_handlers.py`: dispatch new command types
- Updates to backend voucher_dispatcher and posting_tasks: pass `as_optional` flag based on `voucher.is_optional_in_tally`
- New backend endpoints implementing /approve-to-regular and /reject-optional from API.md

**Dependencies:** P0.21, P0.26, P0.37.

**Acceptance:**
- AI-extracted ingestions create voucher with `is_optional_in_tally=true`; connector posts as Optional
- POST /vouchers/{id}/approve-to-regular issues connector command, on success updates DB and audit log
- POST /vouchers/{id}/reject-optional issues connector command, on success marks voucher rejected
- Idempotent: re-running approve on an already-Regular voucher returns success without effect

**Tests:**
- `tests/integration/api/test_vouchers_optional_flow.py`
- `connector/tests/integration/test_optional_voucher_xml.py`

#### P0.46b — Ledger ingest from sync_masters connector reply

**Objective:** Persist the ledger + group payload returned by the connector's `sync_masters` command into the `ledgers` table under the correct tenant. Closes a scope hole caught during §7.5 validation: the original P0.21/P0.22/P0.27 work shipped the WebSocket command plumbing and the `sync_masters` handler, but never wrote the returned ledgers anywhere — the backend logged `status=success` and discarded `result["result"]`.

**Files:**
- `backend/app/services/ledger_service.py` — new `LedgerService.upsert_from_sync(ledgers, groups)`
- `backend/app/api/v1/connector.py` — wire ingest into `_drive()` after `send_command` returns `status=success`
- `backend/app/core/audit.py` — add `ledger.sync_failed` to the allowed actions
- `backend/tests/integration/api/test_connector_sync_ingest.py` — new
- `docs/AUDIT.md` — add `ledger.sync_failed` to the action vocabulary
- `docs/VALIDATION_REPORT.md` — extend §7.5 checklist
- `docs/PHASE_0_CLOSEOUT.md` — record the scope-hole audit note

**Dependencies:** P0.17, P0.27.

**Acceptance:**

1. **New service method.** `LedgerService.upsert_from_sync(ledgers: list[dict], groups: list[dict])`. Idempotent on `(company_id, name_normalized)`. Sets `group_name` (denormalized string per current schema), `gstin`, `opening_balance` (default 0 on create; untouched on update), `is_active=True`. Emits one `ledger.created` or `ledger.updated` audit event per row.

2. **Wire into `connector.py` `_drive()`** after `send_command` returns `status="success"`: open a fresh `SessionLocal` for `company.id`, call the service with `result["result"]["ledgers"]` and `result["result"]["groups"]`, commit. Errors during persist **must not silently succeed** — log, raise, and emit a `ledger.sync_failed` audit row on a separate session.

3. **Groups** fold into `ledgers.group_name` as denormalized strings. Do **not** create a `groups` table — the schema already exposes `group_name` as a string column, and the design reasoning for keeping group identity denormalized in Phase 0 is preserved.

4. **Regression test.** Integration test that invokes the persistence helper with a fake `sync_masters` payload, asserts the ledgers exist in the DB under the right tenant, asserts re-running with the same data is a no-op (idempotency), and asserts ledgers under a different tenant are isolated.

5. **Validation checklist update.** `VALIDATION_REPORT.md` §7.5 gains: *"after sync_masters, verify ledgers exist in DB with correct names, groups, and tenant scoping."*

6. **Closeout doc.** `PHASE_0_CLOSEOUT.md` records: *"Scope hole in P0.21/P0.22 caught during validation. Original tasks shipped WebSocket plumbing but not the ingest persistence path. P0.46b shipped to close the gap. Audit: this should have been caught by integration testing during P0.22; no such test existed at the time."*

7. **Re-run validation.** After implementation, re-run the `sync_masters` validation. Confirm ledgers land in the DB. Human can then proceed with Section 7.5 remaining checks.

**Effort:** ~1 day. **Phase 0 closeout blocks on this.**

**Tests:**
- `backend/tests/integration/api/test_connector_sync_ingest.py`

---

## Sequencing for v1.2 additions

The added tasks integrate with the original sequence as follows:

| Original task | New v1.2 task can run | Why |
|---|---|---|
| After P0.07 | P0.37 (voucher columns) | Schema dependency |
| After P0.18 | P0.36 (8 voucher types) | Builds on voucher service |
| After P0.19 | P0.38 (reports) | Needs vouchers complete |
| After P0.38 | P0.39 (mobile reports), P0.40 (dashboard) | UI depends on backend |
| After P0.40 | P0.41 (mobile dashboard), P0.42 (onboarding) | UI depends on data |
| In parallel from P0.15 | P0.44 (push) | Independent |
| In parallel from P0.16 | P0.45 (deletion) | Independent |
| After P0.26, P0.37 | P0.46 (connector Optional) | Needs both |
| After P0.27 + §7.5 validation | P0.46b (sync_masters ingest) | Scope hole caught during validation; blocks Phase 0 closeout |

Total task count: **35 → 46 → 47** (P0.46b added in v1.3 to close the sync_masters ingest gap).


## Phase 0 done checklist

When all 35 tasks pass acceptance, Phase 0 is done. The human-visible deliverable from the top of this document is achievable end-to-end:

✅ Mobile app installable (dev mode via Expo Go)
✅ Register → login → create company → install connector → enroll → sync ledgers → create voucher → posted to Tally → visible in audit log
✅ Test suite ≥ 80 tests passing, coverage ≥ 80%
✅ CI green, all gates active
✅ All 9 architecture documents reflected in code

The validation step (per the workflow) follows: human runs the full validation checklist from `VALIDATION_REPORT.md`. Findings come back to Architect Claude. Phase 1 starts after Phase 0 validation passes.

## Sequencing rule

Tasks are listed in the order Coder Claude executes. Some can be parallelized (P0.21, P0.22 can run in parallel with P0.14–P0.16; P0.29 can start once P0.15 lands). The sequencing above is the safe serialization.

## Out of scope for Phase 0

Explicitly NOT in Phase 0:
- Invoice OCR / extraction (Phase 1)
- Bank statement ingestion (Phase 2)
- SMS parsing (Phase 2)
- Reconciliation engine (Phase 3)
- GSTR-2B reconciliation (Phase 3)
- WhatsApp gateway (Phase 4)
- Web admin console (Phase 5)
- Razorpay billing (Phase 5)
- Multi-company UX polish, advanced reports, analytics
