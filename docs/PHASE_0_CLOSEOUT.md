# Phase 0 Closeout

Phase 0 is complete. All 46 originally numbered tasks from `PHASE_0_TASKS.md` shipped on `main`; two post-validation patches (P0.46b and P0.46c) were filed during §7.5 walk-through to close a scope hole and ship the end-to-end fix. This document records the as-built state.

- Original closing date: 2026-05-12 (provisional; blocked by P0.46b as of 2026-05-15, then by P0.46c as of 2026-05-16)
- Final closeout: 2026-05-16 after §7.5 sync_masters validated end-to-end
- Branch: `main` @ `f0c5fc0`
- Commits in Phase 0: 56 (46 numbered + 10 cross-cutting)
- Code added: 52,031 insertions / 123 deletions across 254 files (pre-P0.46b/c; current ahead-of-origin tip is `f0c5fc0`)

## Task ledger

All 46 tasks, in numeric order, with the commit that landed each.

| Task | Commit | Summary |
|---|---|---|
| P0.01 | `b2ab14b` | Repository bootstrap |
| P0.02 | `083bfb4` | Backend skeleton + config + database |
| P0.03 | `db29203` | Alembic migrations setup |
| P0.04 | `0a95d5c` | Money handling primitives |
| P0.05 | `96c96c1` | Initial users, companies, user_companies models + migration |
| P0.06 | `505c895` | Ledger model + 0002 migration |
| P0.07 | `8b66064` | Voucher + LedgerEntry models + 0003 migration |
| P0.08 | `acc80e0` | AuditLog + idempotency_keys models + 0004 migration |
| P0.09 | `b7a2f64` | Password hashing + JWT primitives |
| P0.10 | `7dbd3ac` | Tenancy dependencies (auth + scoping foundation) |
| P0.11 | `007f235` | Audit emitter + missing-emit lint |
| P0.12 | `c939ecb` | Idempotency handler |
| P0.13 | `7ebc40d` | Error handling middleware (standard envelope) |
| P0.14 | `cce827f` | Auth: `POST /api/v1/auth/register` |
| P0.15 | `0c63e93` | Auth: login + refresh + me + password |
| P0.16 | `47cf15e` | Companies CRUD + members + first tenant_isolation tier |
| P0.17 | `639614d` | Ledgers CRUD + fuzzy search |
| P0.18 | `2afeff8` | Vouchers: `POST /api/v1/vouchers/` |
| P0.19 | `7ee8bc0` | Vouchers: read, list, update, cancel |
| P0.20 | `e263b5b` | Audit logs: `GET /api/v1/audit-logs/` |
| P0.21 | `694b10e` | Connector: salvage `tally_client.py` |
| P0.22 | `8a45aa8` | Connector WS client |
| P0.23 | `682f029` | Connector enrollment endpoint + token issuance + v1.2 Patch 2 |
| P0.24 | `3559e83` | Connector WS endpoint + `ConnectorRegistry` |
| P0.25 | `92a5fc4` | Connector status endpoint |
| P0.26 | `f661074` | Voucher dispatcher + Celery scaffolding |
| P0.27 | `510515e` | Connector sync trigger endpoint |
| P0.28 | `cd92608` | Connector PyInstaller build + connector-build CI |
| P0.29 | `130c5ea` | Mobile: Expo bootstrap + auth screens |
| P0.30 | `0fa0c37` | Mobile: company switcher + connector status card |
| P0.31 | `717942a` | Mobile: ledger list + manual voucher entry |
| P0.32 | `237fbdc` | CI: full pipeline per `TESTING.md` |
| P0.33 | `ba3f43f` | Lint: import-boundary check (`check_imports.py`) |
| P0.34 | `7ca4e6f` | OpenAPI contract test |
| P0.35 | `107de51` | README rewrite + `docs/SETUP.md` |
| P0.36 | `00ba126` | Manual voucher entry: all 8 voucher types |
| P0.37 | `a72e83c` | Voucher Optional/Regular fields and rejection state |
| P0.38 | `ce272cb` | Reports endpoints (trial balance, P&L, balance sheet, outstanding) |
| P0.39 | `8ca3a4e` | Mobile reports screens |
| P0.40 | `49b5f9a` | Dashboard endpoint: `GET /dashboard/home` |
| P0.41 | `55529a0` | Mobile home dashboard screen (v1.2) |
| P0.42 | `0df7aac` | Onboarding checklist endpoint |
| P0.43 | `1f79829` | Mobile onboarding checklist screen + dashboard tile |
| P0.44 | `e73aeb1` | Push notification: device registration + dispatch infra |
| P0.45 | `35c4a94` | Account deletion request (DPDP Phase 0) |
| P0.46 | `1a768f8` | Connector Optional voucher flow: post-as-optional, approve, reject |

## Architectural patches

Three deltas to the v1.2 architecture were applied during Phase 0. Each is documented in-tree; this section just names them.

- **v1.2 Patch 1 — `audit_logs.company_id` made `NULL`-able** (`af61df2`). System events (`user.created`, `user.password_changed`, `user.deactivated`, device and account-lifecycle events) have no tenant scope, so the `NOT NULL` constraint was lifted and a partial index `(company_id, created_at DESC) WHERE company_id IS NOT NULL` replaced the previous full index. See `AMENDMENTS_v1.2.md` §"Patch 1" and the dependency note in `app/models/audit_log.py`.
- **v1.2 Patch 2 — Connector enrollment code storage** (folded into P0.23, `682f029`). `CONNECTOR_PROTOCOL.md` specified the enrollment ceremony but didn't model the storage. `connector_enrollment_codes` (SHA-256 hash of the code, 15-minute expiry, per-company scope) was added so the issued connector token is bound to one company. See `AMENDMENTS_v1.2.md` §"Patch 2".
- **Audit-FK / append-only trigger conflict** (`fd8f2aa`, docs-only). `audit_logs.user_id` is `ON DELETE SET NULL`, but the Layer-2 trigger refuses the resulting UPDATE — so a user with any audit history can never be hard-deleted. `AUDIT.md` now flags this directly. P0.45's deletion flow accommodates it by anonymising the user row rather than DELETE'ing.

## Post-validation patches

Filed after the original 46-task closeout, while Section 7.5 of `VALIDATION_REPORT.md` was being walked through. Both ship on `main`; with P0.46c re-validation passing on 2026-05-16, Phase 0 closeout is final.

- **P0.46b — Ledger ingest from `sync_masters` connector reply.** Scope hole in P0.21/P0.22 caught during validation. Original tasks shipped WebSocket plumbing but not the ingest persistence path: `connector.py` `_drive()` awaited the connector reply and logged `status=success`, but `result["result"]` (the `ledgers` + `groups` payload) was discarded, and `LedgerService` had no bulk-upsert. P0.46b adds `LedgerService.upsert_from_sync` (idempotent on `(company_id, name_normalized)`), wires it into `_drive()` with a `ledger.sync_failed` audit on persistence error (no silent success), folds the groups list into denormalized `ledgers.group_name` (no new table), and adds `tests/integration/api/test_connector_sync_ingest.py` covering persistence, idempotency, and tenant isolation. **Audit:** this should have been caught by integration testing during P0.22; no such test existed at the time — the WS-plumbing tests in P0.22 stopped at envelope round-trip and never asserted DB state. The Phase 1 task list should treat "every connector command has an integration test that asserts DB outcome" as a gating policy, not a per-task ask.

- **P0.46c — TDL Collection idiom for `sync_masters` Tally XML.** P0.46b's integration tests all passed but live `sync_masters` still produced 0 ledgers in DB. Diagnosis via existing audit logs (`created=0 updated=0 skipped=0` — mathematically requires `len(ledgers)==0`) localised the bug entirely upstream of P0.46b: the connector's Tally XML. Three connector-side bugs stacked, two latent because the rejected envelope never let the parser run on real data. (1) Envelope: `<TYPE>Data</TYPE><ID>Ledger</ID>` (and `<ID>Group</ID>`) is TallyPrime's idiom for "export ONE by name", not "list all" — returned `<RESPONSE>Unknown Request, cannot be processed</RESPONSE>` (59 bytes). Rewritten to `<TYPE>Collection</TYPE>` with an in-line TDL collection definition (NATIVEMETHOD Name / Parent / PartyGSTIN). (2) Parser XPath: `<NAME>` is an XML attribute of `<LEDGER>` in real Tally responses; the only `<NAME>` child is two levels deep under `<LANGUAGENAME.LIST>` and `ET.find("NAME")` doesn't traverse descendants. Switched to `ledger.get("NAME")`. (3) GSTIN field: `<REGISTRATIONTYPE>` is the registration-type enum (Regular / Composition / Consumer / Unregistered), not the GSTIN string. Switched to `<PARTYGSTIN>`. Bonus: Tally inlines `&#4;` (EOT) as a reserved-master marker on system-defined groups like "Primary"; XML 1.0 forbids that codepoint in text and `ET.fromstring` raised `ParseError` against real responses. Added `_sanitize_tally_xml` at the `_post_xml` boundary (every parser benefits) plus `_strip_tally_ctrl` for any leading control chars that survive entity decoding. Tests added: `connector/tests/integration/test_tally_xml_idioms.py` with two golden fixtures captured live from TallyPrime (`connector/tests/fixtures/tally_responses/ledgers_collection.xml` + `groups_collection.xml`). Synthetic XML still in `tests/unit/test_tally_client.py` was updated to match real Tally shape — the old synthetic fixture encoded the same bug as the parser, which is why P0.46b's tests passed against it. **Audit:** the test class P0.46b shipped without was *parser-against-real-Tally-XML*. Synthetic fixtures alone can't catch parser/XML-shape disagreements when the fixture itself encodes the bug. Phase 1 gating policy: every Tally command needs a golden response captured live, plus a parser test that loads it.

- **P0.46d — Manual voucher creation skips Tally dispatch.** (Filed 2026-05-16 during §7.5 voucher-while-Tally-stopped walk-through; see "Validation findings during closeout" below.) Live repro: `POST /api/v1/vouchers/` with the connector connected but TallyPrime stopped returned `status="posted"` and `tally_posted_at=null`, with a single `voucher.created` audit row and no `voucher.posted_to_tally` / `voucher.tally_post_failed` / `voucher.posted_as_optional` follow-up. **Root cause:** `VoucherService.create()` was setting `status=VoucherStatus.posted` at creation time, conflating "DB row written" with "voucher accepted by Tally". The dispatcher was wired but only stamped `tally_posted_at` on success — it never owned the status lifecycle. **Fix (2026-05-17):** split the two signals along the v1.3 model (see `AMENDMENTS_v1.3.md`). `status` is now "live in the books" (`posted` or `pending_tally_post`); `tally_posted_at` is the orthogonal "mirrored to Tally" signal. Shipped as a bundle so the lifecycle change doesn't drop vouchers out of reports: (a) new enum value `pending_tally_post` + new column `tally_post_queued_at` (migration `0010`); (b) `VoucherService.create()` lands the voucher in `pending_tally_post` with `tally_post_queued_at=now()`; (c) `dispatch_voucher_to_tally` transitions `pending_tally_post → posted` only on Tally success; (d) retryable failures (`ConnectorOffline` / `CommandTimeout`) emit the v1.3 `voucher.tally_post_queued` audit action (non-retryable connector errors keep emitting `voucher.tally_post_failed`); (e) 5 report/dashboard sites updated to filter `status.in_([posted, pending_tally_post])` so book-truthful trial balance / P&L / cash tile / GST liability / onboarding checklist all reflect queued entries; (f) `VoucherOut` and `VoucherListItem` now expose `tally_post_attempts`, `tally_last_error`, `tally_post_queued_at`; (g) mobile `VoucherListScreen` renders a "Queued for Tally" badge (distinct from "Posted to Tally" / "Cancelled" / "Rejected") and surfaces `tally_last_error` inline. The v1.3 `tally_post_expired` enum value is intentionally deferred to Phase 0.5 P0.54 (the 30-day expiry beat task is its only use site). **Audit:** the test class P0.46d shipped without was "voucher created while Tally is down → status != posted AND a dispatch-failure audit row exists." Test suite still asserted DB outcomes for the happy path but not for the offline-Tally branch; the new `test_dispatch_offline_keeps_pending_and_emits_queued_audit` and the report-inclusion regression closes both gaps.

## Validation findings during closeout

P0.46b, P0.46c, and P0.46d are all scope holes that the pre-validation test suite could not catch, and that live validation against TallyPrime surfaced in succession. The pattern is consistent enough to record here as a process finding, not just three individual patches.

- **P0.46b** — backend persistence path for connector replies never existed; the WS-plumbing tests in P0.22 stopped at envelope round-trip and never asserted DB state.
- **P0.46c** — the Tally XML envelope used by the connector was being rejected outright by TallyPrime, and two parser bugs would have masked the envelope fix had the envelope ever worked. The unit-test fixture encoded the same wrong-shape XML as the parser read, so the test passed against synthetic data that no real Tally would ever produce.
- **P0.46d** — manual voucher creation never invoked the Tally dispatcher at all; `status="posted"` was being written at creation time rather than reflecting any Tally interaction. The backend integration tests asserted "voucher created → DB row exists" but never asserted "voucher created while Tally is down → status != posted AND a dispatch-failure audit row exists."

**Common factor:** the synthetic test infrastructure assumed Tally integration *worked* and tested the code paths around it; nothing in the suite ever verified that bytes actually flowed to or from a running Tally instance. Every connector-dependent feature appeared green in CI because its tests stopped at the seam between application code and Tally.

**Phase 1 gating policy proposal.** Every connector-dependent feature must have at least one live-Tally integration test before being marked complete. The test must drive the full path — backend endpoint → dispatcher → WS → connector handler → Tally XML over HTTP → real TallyPrime response (or a recorded golden fixture from one) → parser → audit row + DB state — and assert against the *observable* outcome, not just the intermediate function call. This is a stronger bar than "every connector command has an integration test that asserts DB outcome" (P0.46b's lesson) because it forbids the synthetic-fixture trap that P0.46c hit. Concrete acceptance criterion: the integration test fails when the connector binary, the dispatcher wiring, or the XML idiom is broken — independently and detectably. Tracked as a Phase 1 prerequisite, not a per-task ask.

**Update 2026-05-19/20 — five additional findings in the same family.** Live validation of §7.5b (the voucher dispatch path with TallyPrime up *and* the Celery worker running, neither of which §7.5 sync_masters or §7.5a had exercised) surfaced five more scope holes between 2026-05-19 and 2026-05-20. Each was found by the live integration; none was visible to the pre-validation test suite. Filed as memory entries `BUG-Books-001` through `BUG-Books-004` plus a routing-hygiene patch landed as P0.58.

- **BUG-Books-001** — A PowerShell-driven `POST /api/v1/vouchers/` produced a duplicate-create+cancel during §7.5a: a UTF-16 BOM in a here-string body returned HTTP 400 to the client while the server-side write still succeeded. Harmless with Tally offline (the cancel raced ahead of any dispatch); with a running worker the same pattern could land a duplicate in Tally before the client's retry-without-Idempotency-Key arrived. Open P1; must be understood before §7.5b's idempotency-replay checkbox.
- **BUG-Books-002** — Once `post_voucher_to_tally` retries exhaust (`max_retries=5`; window ranges from ~31 seconds at the unjittered floor to ~50 minutes if the exponential backoff saturates at `retry_backoff_max=600s`) against an offline connector, the voucher stays `status=pending_tally_post` indefinitely. No scheduled sweep, no connector-up event, no admin endpoint. A CA whose laptop is closed for lunch or restarted by Windows Update has every voucher posted during the outage silently stranded; the books-vs-Tally drift surfaces only at month-end reconciliation. Open P1 architectural; Phase 0.5 hardening.
- **BUG-Books-003** — `connector_registry` is process-local to the API uvicorn (its own docstring says so: "Phase 0 keeps this process-local"); the Celery worker process gets an empty registry instance and always raises `ConnectorOffline`. Every voucher dispatched via the documented worker path strands regardless of whether the connector is actually connected — production unusable as shipped. Phase 0 mitigation in commit `aea1f10`: `CELERY_TASK_ALWAYS_EAGER=1` routes dispatch back into the uvicorn process (the registry's owner). Phase 0.5+ proper fix: Redis pub/sub fan-out per the registry docstring.
- **P0.58 — operational env propagated outside `Settings`.** Patch-level routing-hygiene fix landed in `5c2fa25` → `aea1f10` → `e298279`. The BUG-003 eager-mode hotfix initially read `CELERY_TASK_ALWAYS_EAGER` via raw `os.environ`, bypassing the `pydantic-settings` instance that loads `.env`. The flag appeared set in PowerShell but didn't take effect because the backend's effective config never read it. Resolved by routing `CELERY_TASK_ALWAYS_EAGER`, `CONNECTOR_COMPANY_ID`, and `TAXMIND_SKIP` through `Settings`. Same family: a feature path shipped, but its operational scaffolding (which env var, read by which process, via which loader) was never tested end-to-end.
- **BUG-Books-004 — silent Tally posting failure.** Three independent bugs in the connector + dispatcher response path; **Layer A is P0 architectural and a Phase 1 ship-blocker.** `connector/tally_client.post_voucher` returns `status:"success"` on any HTTP 200 regardless of Tally's `<CREATED>0</CREATED>` / `<EXCEPTIONS>1</EXCEPTIONS>` structured-rejection envelope — backend writes `voucher.posted_to_tally` for vouchers Tally explicitly rejected. Confirmed on the *documented happy path* (voucher `7b1e4328-…`, 2026-05-20): Tally up, Taxmind Books loaded, six ledgers present including the two the voucher references; backend reports `status=posted` while a direct Day Book probe to `:9000` returns `<VOUCHER>0</VOUCHER>` for the date range. Layer A is the default behaviour for *any* Tally-side rejection, not an edge case. Layers B and C are P1 / Phase 0.5: the dispatcher ignores the connector envelope's `retryable` field (misclassifies `TallyUnreachable` as a non-retryable `voucher.tally_post_failed`); a `tally_voucher_guid` key mismatch between connector return shape and dispatcher reader means no durable Tally pointer is ever persisted, even for real successes.

**Common factor (extended).** The 2026-05-19/20 findings sit in the same family as P0.46b/c/d: synthetic test infrastructure systematically tested the assumed-correct path, and live integration found gaps the synthetic infrastructure could not catch. The new findings push the pattern in two directions. (a) The Tally-side rejection envelope (`<CREATED>0</CREATED>` / `<EXCEPTIONS>1</EXCEPTIONS>` / `<LINEERROR>`) was never modelled on either side of the boundary — the connector's unit fixtures cover `TallyUnreachable` and `TallyResponseError` (transport / non-200) but not "200 with structured rejection in the body", and the backend dispatcher's fixture envelopes only ever carry `status:"success"`. (b) The deploy-shape assumption (worker process and API process share in-memory state) was hard-coded by `task_always_eager=True` in the test suite, so the worker/API process split never ran in CI; production runs the two as separate processes by default. BUG-003 and BUG-004 Layer A are the worst of the cluster because they produce *silent corruption* — books-side state claims success with the correct-looking happy-path audit row written, and no error surface alerts the operator. The earlier P0.46b/c/d findings at least left a visible failure (zero ledgers ingested, `status="posted"` with a null `tally_posted_at`); BUG-003 and BUG-004 Layer A do not.

**Phase 0.5+ process recommendation (extension of the Phase 1 gating policy above).** The Phase 1 gating policy demands one happy-path live-Tally test per connector-dependent feature. That is necessary but not sufficient for the dispatch pipeline specifically (connector → dispatcher → audit chain → reports), which is the most fragile surface in the system and the source of every 2026-05-19/20 finding. Any Phase 0.5+ change touching that pipeline must additionally have at least one live-Tally integration test with *deliberate failure injection*. Required failure modes to exercise: Tally down (port 9000 not listening); Tally up but no company loaded; Tally up but a *different* company loaded than the voucher's; network drop mid-post; connector offline at dispatch time. For each, the test must assert that the failure is *visible to the operator* — a `voucher.tally_post_failed` or `voucher.tally_post_queued` audit row with a populated `tally_last_error`, or a non-`posted` voucher status surfaced with the right badge in mobile. The acceptance criterion is symmetric: the happy-path test fails if dispatch is broken; the failure-injection test fails if a real Tally-side rejection produces a `voucher.posted_to_tally` audit row. Voucher `7b1e4328-…` is the canonical example of what failure injection must catch — a voucher Tally explicitly rejected, written to the books as posted with no error surface. This is the testing complement of Phase 0.5's architectural hardening on the same surface (BUG-002 re-enqueue, BUG-003 registry pub/sub fan-out, BUG-004 response-body parsing).

## Test totals

| Tier | Tests | Suite command |
|---|---|---|
| Backend (`backend/tests`) | 519 passed | `pytest tests/integration/ tests/unit/` |
| Connector (`connector/tests`) | 51 passed | `pytest` |
| Mobile (`mobile`) | 35 passed (10 suites) | `npm test` |

Totals are full-suite green at `f0c5fc0`. Tenant-isolation tests (`backend/tests/tenant_isolation/`) are part of the backend total but live in their own marker. The connector total rose from 47 to 51 with P0.46c's golden-fixture suite.

## Code volume

| Path | Files | Insertions |
|---|---|---|
| `backend/` | 168 | 24,449 |
| `connector/` | 18 | 2,716 |
| `mobile/` | 51 | 23,056 |
| `tools/` | 5 | 973 |
| `docs/` | 5 | 446 (–6) |

Aggregate: 254 files, 52,031 insertions, 123 deletions, **9 alembic migrations** (0001 → 0009).

## Known issues

- **BUG-Books-003 — Celery worker process holds an empty `connector_registry` instance.** The registry is process-local to the API uvicorn (`backend/app/services/tally/connector_registry.py:12`: "Phase 0 keeps this process-local"); the worker process gets its own empty instance, so the documented voucher-dispatch path raises `ConnectorOffline` for every voucher regardless of whether the connector is actually connected. **Mitigated in Phase 0** by `CELERY_TASK_ALWAYS_EAGER=1` routed through `Settings` (commit `aea1f10`); **Phase 0.5 must resolve** via Redis pub/sub fan-out per the registry docstring (option 2 in the P0.58 follow-up). Cluster context: "Validation findings during closeout" §"Update 2026-05-19/20" above.
- **BUG-Books-004 Layer A — connector treats any HTTP 200 from Tally as success without parsing the response body.** `connector/tally_client.post_voucher` returns `status:"success"` regardless of Tally's `<CREATED>0</CREATED>` / `<EXCEPTIONS>1</EXCEPTIONS>` structured rejection — backend writes `voucher.posted_to_tally` for vouchers Tally explicitly rejected. Silent books-vs-Tally drift with no error surface. **Phase 1 ship-blocker; fix not started.** Reproducers `5b7e2573-…` (Tally up with no company loaded, 2026-05-19) and `7b1e4328-…` (Tally up with Taxmind Books loaded on the documented happy path, 2026-05-20; Day Book probe to `:9000` returned `<VOUCHER>0</VOUCHER>` post-POST) preserved as evidence — do not cancel or repost. Cluster context: "Validation findings during closeout" §"Update 2026-05-19/20" above.
- **`audit_logs.user_id` cascade is dead.** Documented above. Effect on Phase 0: `account_lifecycle_service.process_due_deletion` anonymises rather than hard-deletes. Effect on Phase 1+: any future code that calls `DELETE FROM users` will hit `audit_logs is append-only` and roll back; the only safe paths are tightening the trigger via `pg_trigger_depth()` or toggling `session_replication_role = 'replica'`. Either deserves an `AMENDMENTS_v1.2.md` patch first.
- **Migration numbering diverges from `PHASE_0_TASKS.md`.** Doc spec referenced `0011_device_tokens` and `0012_account_deletion_requests`; the actual migrations are sequential (`0008`, `0009`). The doc was the stale side. Not a runtime issue.
- **Test-suite ordering sensitivity, worker tier.** A full `pytest tests/` run with discovery across `tenant_isolation/` plus `workers/` can show transient `test_posting_task` failures driven by shared registry state. `pytest tests/integration/ tests/unit/` is green; the isolated subset and rerun are green. The class of failure is in cross-suite global state, not in any tested behaviour.
- **Dashboard "today" computed in UTC, not IST.** `app/services/dashboard_service.build_dashboard` does `now = datetime.now(UTC); today_local = now.date()` (variable name is misleading — the value is the *UTC* date). For ~5.5h every night (00:00–05:29 IST = 18:30–23:59 UTC of the prior calendar day), `GET /api/v1/dashboard/home` shows the *previous* IST day's data labelled as "today" — `today.cash_in`, `today.cash_out`, `today.vouchers_created`, `today.vouchers_pending_approval`, and `gst_liability_indicative.month_to_date` on the first of any month are all off-by-one. Affects every India user. This is also what caused the 2026-05-12 18:30 UTC validation run to record 4 `test_dashboard.py` failures (`test ... cash_in_out_aggregates_bank_movement`, `... today_voucher_counts`, `... outstanding_totals`, `... gst_liability_mtd_is_output_minus_input`); the tests have since been pinned to the same UTC clock as the service (commit `024d6e4`) so they no longer flake at the boundary, but the underlying production bug is unchanged. **Resolution path:** introduce a company-level timezone column (default `Asia/Kolkata`), expose it through `Company`, and have `dashboard_service` and every endpoint listed in the audit below compute day boundaries in that zone. **Owner:** Phase 1 — add as `P1.NN — TZ-aware date boundaries` when the Phase 1 task list opens.
- **Inconsistent "today" defaults across endpoints (audit).** Surfaced while diagnosing the dashboard bug above. Reports endpoints fall back to a different clock than the dashboard does, so a user hitting both at 00:00 IST gets two different "today"s.

  | Endpoint | "Today" / date default | Timezone | Source |
  |---|---|---|---|
  | `GET /api/v1/dashboard/home` | `datetime.now(UTC).date()` | **UTC** | `app/services/dashboard_service.py:132–134` |
  | `GET /api/v1/reports/trial-balance` | `_date.today()` when `as_of_date` omitted | **server local** | `app/api/v1/reports.py:79` |
  | `GET /api/v1/reports/profit-loss` | `_date.today()` when `to_date` omitted; `from_date` defaults to `fiscal_year_start(to_date)` | **server local** | `app/api/v1/reports.py:125–126` |
  | `GET /api/v1/reports/balance-sheet` | `_date.today()` when `as_of_date` omitted | **server local** | `app/api/v1/reports.py:171` |
  | `GET /api/v1/reports/outstanding` | `_date.today()` when `as_of_date` omitted | **server local** | `app/api/v1/reports.py:239` |

  In production today, "server local" is the deploy environment's TZ — currently UTC on the planned Linux hosts, so reports happen to agree with the dashboard there. The risk is two-fold: (a) the moment any deploy lands on a non-UTC host (CI, a developer laptop, a future region) reports and dashboard diverge silently; (b) the *correct* timezone for India users is `Asia/Kolkata` for both, and the Phase 1 fix above must rewrite both call sites — not just the dashboard. The reporting services themselves (`balance_sheet.py`, `profit_loss.py`, `trial_balance.py`, `outstanding.py`) take `as_of_date`/`from_date`/`to_date` as parameters and contain no clock reads, so they don't need to change; only the endpoint defaults do.

## Deferred to Phase 1

Items deliberately stubbed in Phase 0; each has a named stub the Phase 1 worker can replace without rewiring callers.

- **Real FCM / APNs clients.** `app/integrations/fcm_client.py` and `apns_client.py` are no-op shims that log intent and return `delivered=True`. The dispatch graph (`notification_service.send_to_user`) is wired against them; Phase 1 swaps in HTTP/2 calls + provider creds without touching service or API code.
- **Real email infrastructure.** `account_lifecycle_service._send_account_email` is a log-only stub called on deletion request / cancellation / completion. Phase 1 plugs the real provider (SES / Postmark) when one is chosen.
- **Data-export-on-delete.** `account_deletion_requests.final_export_s3_key` is declared in the schema and left `NULL` in Phase 0. Phase 1's `POST /api/v1/account/data-export` flow will populate it before the grace period ends.
- **`first_invoice_extracted` checklist item.** The onboarding checklist (P0.42) exposes the item with `completed=False` always; the underlying `ingestions` table is a Phase-1+ artefact.

## Follow-ups

Small items that didn't justify their own task but should land before they accumulate.

- Reconcile `SCHEMA.sql` with the actual `ON DELETE` clauses in the migrations (the schema doc omits the cascade behaviour on `audit_logs.user_id` that migration 0004 added).
- Decide the long-term answer for `audit_logs.user_id` cascade vs the append-only trigger; pick one of the two paths in `AUDIT.md` §"Interaction with `audit_logs.user_id ON DELETE SET NULL`".
- Wire a real Celery beat schedule for `app.workers.process_due_account_deletions` (daily); the task ships in Phase 0 but production scheduling is deploy-side and not yet expressed in code.
- The full mobile test surface is 35; tenant-isolation and accessibility passes are sparse. Phase 1 can add depth without restructuring.

---

End of Phase 0.
