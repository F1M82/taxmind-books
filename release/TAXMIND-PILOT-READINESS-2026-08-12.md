# TaxMind Books — Controlled Pilot Readiness Decision

**Document:** `release/TAXMIND-PILOT-READINESS-2026-08-12.md`
**Date:** 2026-08-12
**Prepared for:** Founder / pilot operator
**Scope:** Final go/no-go for a controlled, invite-only pilot of TaxMind Books.
**Sources combined:** (1) VPS deployment & security review, (2) TaxMind operational closeout (`docs/PHASE_0_CLOSEOUT.md`), (3) automated validation report (`docs/VALIDATION_REPORT.md` + CI runs), (4) live Tally validation report (this session, 2026-08-12 ~11:30–11:40 UTC).

---

## FINAL DECISION: **READY FOR CONTROLLED PILOT**

"READY FOR CONTROLLED PILOT" means exactly and only:

- **invite-only / known pilot users** — no unrestricted public signup; pilot participants are identified and consented in advance;
- **test or consented pilot data only** — no real client accounting data is processed unless the pilot user has explicitly consented;
- **monitoring required** — the pilot runs under active observation (logs, health probes, backup verification), not unattended;
- **production-readiness is a separate future gate** — passing this decision does not constitute "fully production ready"; unrestricted public rollout is a later, independently-gated decision.

The system is **not** declared "fully production ready." A deferred engineering backlog (§20) and a set of accepted pilot risks (§19) remain.

---

## Severity classification (this decision)

| Class | Count | Meaning |
|---|---|---|
| **CRITICAL** | **0** | Issues that block the pilot. None. |
| **HIGH** | **0** | Issues that require action before the pilot starts. None. |
| **PILOT BLOCKERS** | **0** | Issues that prevent the pilot from proceeding. None. |

---

## Verification classification (used throughout)

- **VERIFIED PASS** — exercised live or by automated suite; evidence captured.
- **ACCEPTED PILOT RISK** — known limitation accepted for the pilot under the controlled scope above; not a blocker.
- **DEFERRED PRE-PRODUCTION WORK** — engineering work scheduled before public-rollout readiness; does not block the controlled pilot.
- **NOT TESTED / OUT OF SCOPE** — not exercised in this validation cycle; excluded from the pilot scope or deferred by design.

---

## 1. Executive decision

TaxMind Books passes all controlled-pilot gates. The live Tally integration pipeline — enrollment, WebSocket, master sync, voucher dispatch, idempotency, reconnect recovery, tenant isolation, and audit trail — is **VERIFIED PASS** on live TallyPrime against a dedicated test fixture (this session, 2026-08-12). The production VPS hosts a healthy backend over HTTPS with verified TLS, DNS, Caddy, isolated Postgres/Redis, a working backup + restore, revoked SSH recon access, and a passed health soak during which the co-hosted GC Wealth site remained healthy. No CRITICAL, HIGH, or pilot-blocking issues are open.

The pilot proceeds under invite-only, consented-data, monitored conditions. The known issues that remain (§18) are either mitigated for the pilot (e.g., `CELERY_TASK_ALWAYS_EAGER` for the single-instance pilot deploy) or are accepted as bounded pilot risk. Full public-rollout readiness is a separate future gate that depends on the §20 backlog.

## 2. Production / VPS deployment status — **VERIFIED PASS**

- VPS deployment completed; backend live over HTTPS at `books.gcwealthguru.com`.
- WebSocket (`wss://…/api/v1/connector/ws`) verified publicly reachable and upgradeable.
- Combined VPS health soak passed; GC Wealth (co-hosted) remained healthy throughout the soak.
- Single backend instance (pilot topology) — matches the in-memory `connector_registry` assumption (see §19 / §20 for the multi-instance pre-production work).

## 3. TLS / DNS / Caddy status — **VERIFIED PASS**

- Caddy fronts the backend with automatic Let's Encrypt TLS.
- `https://books.gcwealthguru.com` serves the backend; `wss://…/connector/ws` completes the WebSocket upgrade over TLS.
- DNS A record for `books.gcwealthguru.com` resolves to the VPS; TLS certificate is valid and auto-renewing.

## 4. Docker / network isolation — **VERIFIED PASS**

- TaxMind services run in isolated Docker networks; the TaxMind backend, Postgres, and Redis containers are not reachable from the GC Wealth stack except through explicitly published ports.
- Co-hosted GC Wealth site remained healthy throughout the TaxMind deploy and the combined health soak — no resource contention or port collision observed.

## 5. Database / Redis isolation — **VERIFIED PASS**

- TaxMind PostgreSQL (`taxmind-postgres`, image `postgres:16-alpine`, port 5432) and Redis (`taxmind-redis`, port 6379) are isolated from the GC Wealth data stores.
- Distinct databases, distinct containers, distinct credentials. No shared schemas, no shared connection pools.

## 6. Backup + restore status — **VERIFIED PASS**

- Backup timer active on the VPS.
- A real backup artifact was produced and successfully restored into a scratch database, confirming the backup is usable end-to-end (not just that a file was written).

## 7. SSH / security status — **VERIFIED PASS**

- SSH recon access used during deployment has been revoked.
- No standing recon credentials remain. Access to the VPS is limited to the operator's normal, audited channel.

## 8. Automated test results — **VERIFIED PASS**

| Tier | Result | Source |
|---|---|---|
| Backend | **605 tests passed**, coverage **94.62%** | `pytest` (CI + local) |
| Connector | **103 tests passed** | `pytest` (CI + local) |
| Mobile | **TypeScript clean + 35 Jest tests passed** (10 suites) | `npm test` |
| Tenant isolation | **15 tenant-isolation tests passed** | dedicated marker |

- Lint, type-check, and contract checks green. Full-suite totals are CI-verified.
- Suite totals agree with `docs/PHASE_0_CLOSEOUT.md` §"Test totals" within the phase's incremental growth.

## 9. Live Tally validation — **VERIFIED PASS** (this session, 2026-08-12)

End-to-end validation against live TallyPrime with the dedicated **test fixture** company loaded ("Taxmind Books": ledgers ABC LTD / Cash / HDFC BANK / Profit & Loss A/c / Xyz Ltd, GUID prefix `ed86199b-…`). TallyPrime was running on the validation host (port 9000 open); the test fixture was confirmed by a read-only probe before any write.

| Live test | Result | Evidence summary |
|---|---|---|
| Enrollment | **VERIFIED PASS** | Owner-issued code → 201 (15-min TTL); anonymous enroll → 200, `connector_id=003e96c1-…`, 365-day JWT |
| Connector authentication | **VERIFIED PASS** | `GET /connector/status` → `connected=true, tally_running=true, connector_version=0.1.0, connector_build_sha=803921c` |
| WebSocket | **VERIFIED PASS** | WS established; heartbeat-driven status live |
| Master sync | **VERIFIED PASS** | 5 ledgers persisted with `tally_master_id` GUIDs, correct group_name, 0 null (BUG-Books-005 fix live); idempotent re-run 5→5, GUIDs unchanged, 0 `ledger.sync_failed` |
| Tally → TaxMind voucher | **NOT TESTED / OUT OF SCOPE** | The frozen `CONNECTOR_PROTOCOL.md` defines no voucher-ingestion message; master sync is the Tally→TaxMind data flow and is verified. Out of scope by design. |
| TaxMind → Tally dispatch | **VERIFIED PASS** | `POST /vouchers/` Receipt 100 (HDFC BANK Dr / Xyz Ltd Cr) → `pending_tally_post → posted` in ~37 ms; audit `voucher.created`→`voucher.posted_to_tally`; Tally Day Book confirms voucher #10 with exact narration and correct debit/credit sides |
| Idempotency / retry | **VERIFIED PASS** | Same Idempotency-Key replayed → HTTP 201, header `idempotent-replay: true`, same voucher id returned; TaxMind still 1 voucher; Tally Day Book still only #10 (no duplicate) |
| Connector reconnect / recovery | **VERIFIED PASS** | Connector killed → `/status` `connected=false`; posted stranded voucher → `pending_tally_post`, audit `voucher.tally_post_queued`, retry error `"no active connector for company …"`; relaunched connector → `connected=true` → stranded voucher auto-posted with **no manual retry** (BUG-Books-002 re-enqueue hook); Tally Day Book now voucher #11 |
| Tenant isolation | **VERIFIED PASS** | WS handshake valid token + mismatched `X-Company-ID` → **403 Forbidden**; API cross-company GET/POST vouchers & status (non-member company) → **404 `company_not_found`**; cross-company POST created **0** vouchers in the non-member company |
| Audit trail | **VERIFIED PASS** | Per-company audit: `company.created(1)`, `ledger.created(5)`, `voucher.created(2)`, `voucher.posted_to_tally(2)`, `voucher.tally_post_queued(1)`; per-voucher lifecycle rows present and correctly sourced (`api` / `worker`) |

Prior §7.5/§7.6 validation history (recorded in `docs/VALIDATION_REPORT.md`): §7.5a PASSED live 2026-05-18; §7.5b happy-path PASSED live 2026-07-21; §7.5b rejection-lane PASSED live 2026-05-22; BUG-Books-004 Layer C (`tally_voucher_guid`/REMOTEID-on-Create) PASSED live 2026-07-21. This session re-verified the full §7.5b + §7.6 backend checklist end-to-end. **§7.6 mobile end-to-end (full Expo render on a device) remains NOT TESTED this session** — see §18.

## 10. Enrollment — **VERIFIED PASS**

Documented two-step ceremony (`CLAUDE.md` §"Connector enrollment"; `docs/CONNECTOR_PROTOCOL.md` §"Connector token"):
1. Owner issues a one-time enrollment code via `POST /api/v1/connector/enrollment-codes` (requires owner role + `X-Company-ID` + `Idempotency-Key`; 15-minute TTL; SHA-256-hashed, single-use).
2. Connector exchanges the code anonymously via `POST /api/v1/connector/enroll` → receives a 1-year connector JWT bound to the company.

Live evidence: code `9gFegx1k…` issued (201), exchanged (200) for `connector_id=003e96c1-…`, `company_id=5dd7fc69-…`, JWT `exp`=2027-08-12. A transient HTTP 422 on the first enroll attempt was a client-side JSON-quoting artifact (PowerShell→`curl`); re-sent with `--data-binary @file` → 200. **Not an application defect.** The code was not consumed by the failed parse.

## 11. Master synchronization — **VERIFIED PASS**

- `sync_masters` pulls ledgers + groups from Tally and persists them under the tenant via `LedgerService.upsert_from_sync` (idempotent on `(company_id, name_normalized)`).
- Live: 5 ledgers persisted with durable Tally GUIDs (`tally_master_id` = `ed86199b-…-000000{cd,1f,cf,1e,ce}`), correct `group_name`, 0 null `tally_master_id`. Confirms the BUG-Books-005 fix (Tally GUID as durable identifier, four-arm NULL/GUID reconcile matrix, `tally_synced_at` stamp) live.
- Idempotent re-run of `sync_masters`: ledger count 5→5, 5 distinct GUIDs unchanged, 0 new `ledger.created` rows, 0 `ledger.sync_failed`. Upsert skipped all 5.
- Two-layer voucher post guard (`check_ledgers_synced`, fresh query every call) rejects any voucher referencing an unsynced ledger — defense verified implicitly by the dispatch test (all referenced ledgers were synced).

## 12. Voucher dispatch (TaxMind → Tally) — **VERIFIED PASS**

- `POST /api/v1/vouchers/` creates the voucher in `pending_tally_post` with `tally_post_queued_at` set and `voucher.created` audit; the dispatcher (eager in the pilot single-instance topology) sends `command: post_voucher` over the WS; on Tally success the voucher transitions `pending_tally_post → posted`, `tally_posted_at` is set, `tally_voucher_guid` is persisted (== voucher id under BUG-004 Layer C REMOTEID-on-Create), and `voucher.posted_to_tally` audit is written.
- Live: Receipt voucher `a3ba7c4d-…` (HDFC BANK Dr 100 / Xyz Ltd Cr 100) → `posted` in ~37 ms. Tally Day Book: voucher **#10**, Receipt, narration `"live validation: HDFC Dr / Xyz Cr 100"` (exact match), entries HDFC BANK (Deemed Positive=Yes/Dr, 100.00) and Xyz Ltd (Deemed Positive=No/Cr, −100.00).
- Rejection lane (recorded 2026-05-22): a voucher Tally explicitly rejects (`<LINEERROR>Ledger 'Sales' does not exist!</LINEERROR>`) produces `voucher.tally_post_failed` with `error.code=TallyImportRejected` and **no phantom voucher** in Tally. The connector parses the ImportData response envelope (BUG-Books-004 Layer A fix, commit `4832688`); the backend dispatcher reads `envelope.retryable` and raises `TallyRetryableEnvelope`/`TallyRejectedEnvelope`.

## 13. Idempotency — **VERIFIED PASS**

- Backend deduplicates HTTP requests by `(user_id, Idempotency-Key)` (`docs/IDEMPOTENCY.md`); the connector deduplicates Tally posts by `(command, idempotency_key)` in a local SQLite cache (24-hour TTL).
- Live replay: re-POST of the dispatch voucher body with the **same** `Idempotency-Key` returned HTTP 201, header **`idempotent-replay: true`**, and the **same** voucher id `a3ba7c4d-…`. The replay short-circuited before dispatch: TaxMind remained at 1 voucher and Tally Day Book remained at voucher #10 only (no #11). Network-layer retries between backend and connector therefore cannot double-post to Tally.

## 14. Connector reconnect / recovery — **VERIFIED PASS**

- Controlled kill of the connector → `/connector/status` flipped to `connected=false` (backend dropped the stale WS from the in-memory registry after the heartbeat lapsed).
- Voucher posted while offline → `pending_tally_post`, audit `voucher.tally_post_queued` (source `worker`), retry attempt recorded `tally_last_error="no active connector for company 5dd7fc69-…"` (retryable, not a hard failure).
- Connector relaunched (fresh-enrolled token) → `connected=true` → the stranded voucher **auto-posted with no manual retry**: `pending_tally_post → posted`, `tally_posted_at` set, audit `voucher.posted_to_tally` written (BUG-Books-002 connector-up re-enqueue hook). Tally Day Book: voucher **#11**, narration `"…stranded-then-reconnect…"`.
- Disconnect-detection latency is heartbeat-driven (connector sends heartbeat every 30 s; backend removes the connection after a 90 s heartbeat lapsed). Reconnect uses exponential backoff capped at 60 s with ±20% jitter (per `CONNECTOR_PROTOCOL.md`).

## 15. Tenant isolation — **VERIFIED PASS**

- Connector-token-bound-to-company: a WS handshake with a **valid** connector token issued for `5dd7fc69-…` and a **mismatched** `X-Company-ID` (`58f01ad7-…`) → **HTTP 403 Forbidden**. The token's `company_id` is checked against the `X-Company-ID` header at upgrade time.
- API-layer multi-tenancy: an authenticated user who is a member of `5dd7fc69-…` but **not** a member of `58f01ad7-…` receives **404 `company_not_found`** for `GET /vouchers/`, `POST /vouchers/`, and `GET /connector/status` when targeting the non-member company. A cross-company POST created **0** vouchers in the non-member company.
- Backend never trusts connector-supplied `company_id` in command results; results are matched by `request_id` to the pending command issued for the known company.

## 16. Audit trail — **VERIFIED PASS**

- Append-only `audit_logs` table (`docs/AUDIT.md`). Every state transition emits a row.
- Live per-company audit for the validation company: `company.created(1)`, `ledger.created(5)`, `voucher.created(2)`, `voucher.posted_to_tally(2)`, `voucher.tally_post_queued(1)`.
- Per-voucher lifecycle, dispatch voucher: `voucher.created` (api) → `voucher.posted_to_tally` (worker), ~37 ms apart — a real dispatch, not a stamp-at-creation.
- Per-voucher lifecycle, stranded voucher: `voucher.created` (api) → `voucher.tally_post_queued` (worker) → `voucher.posted_to_tally` (worker) — full queued-then-recovered path.
- Connector operations carry `source="connector"`/`"worker"` to distinguish from `api` events (per the protocol's security property #5).

## 17. Resource / soak results — **VERIFIED PASS**

- Combined VPS health soak passed; backend, Postgres, and Redis steady under sustained probing.
- GC Wealth (co-hosted on the same VPS) remained healthy throughout the TaxMind deploy and the soak — no resource starvation, port collision, or DB contention.
- No memory leak, connection-pool exhaustion, or CPU saturation observed during the soak window.

## 18. Known limitations — **ACCEPTED PILOT RISK** (unless noted DEFERRED)

- **Single backend instance required (BUG-Books-003).** The in-memory `connector_registry` is process-local; a separate Celery worker process would hold an empty registry and strand every voucher. **Pilot mitigation:** `CELERY_TASK_ALWAYS_EAGER=1` routes dispatch back into the uvicorn process (the registry's owner). Validated live this session (the stranded-then-reconnect test dispatched and posted via the eager path). **DEFERRED PRE-PRODUCTION WORK:** Redis pub/sub fan-out for multi-instance deploys (§20).
- **Voucher-ingestion direction (Tally → TaxMind for vouchers) is not implemented.** The frozen v1 protocol carries no voucher-ingestion message; master sync is the only Tally→TaxMind data flow. NOT TESTED / OUT OF SCOPE for the pilot; not a gap against the documented scope.
- **§7.6 mobile end-to-end (full Expo render on a device) NOT TESTED this session.** The mobile suite is green (35 Jest tests, 10 suites), the voucher-list "Queued for Tally"/"Posted to Tally" badge *data contract* is verified against API payloads, but a full on-device Expo render + connector enroll from the app + manual voucher entry from the app was not exercised this session. DEFERRED PRE-PRODUCTION WORK for the pilot operator to walk through on a device.
- **TallyPrime product version not surfaced.** The connector reports `tally_version=null` in `GET /connector/status` (cosmetic; the `tally.exe` Edit-Log module file-version reads `1.1.7.1`, but the connector does not parse and publish Tally's product version). NOT TESTED / OUT OF SCOPE; functional behavior is unaffected.
- **`vouchers.tally_voucher_number` is not persisted** even though Tally assigns and returns it (#10/#11 in this session's Day Book). TaxMind's `voucher_number` column is the user's own numbering; the Tally-assigned number is not mirrored. ACCEPTED PILOT RISK (cosmetic; the durable `tally_voucher_guid` is persisted and is the authoritative Tally pointer).
- **Wrong-company WS close-code is cosmetic.** A mismatched-company WS handshake rejects as HTTP 403 (a pre-`accept()` close) rather than a 4003 close frame as `CONNECTOR_PROTOCOL.md` documents. The rejection holds; the code shape is cosmetic. ACCEPTED PILOT RISK.
- **Dashboard "today" computed in UTC, not IST (P0.31 follow-up).** For ~5.5 h every night (00:00–05:29 IST), `GET /api/v1/dashboard/home` labels the prior IST day's data as "today"; reports endpoints use a different clock. Affects every India user. DEFERRED PRE-PRODUCTION WORK (Phase 1: company-level timezone column, default `Asia/Kolkata`).
- **Push-notification and account-deletion email providers are stubs.** `fcm_client`/`apns_client` are no-op shims that log intent and return `delivered=True`; `account_lifecycle_service._send_account_email` is log-only. DEFERRED PRE-PRODUCTION WORK (Phase 1: real FCM/APNs/SES/Postmark).
- **Test-suite ordering sensitivity in the worker tier.** A full `pytest tests/` discovery across `tenant_isolation/` plus `workers/` can show transient `test_posting_task` failures from shared registry state; the canonical command `pytest tests/integration/ tests/unit/` is green. ACCEPTED PILOT RISK (CI runs the canonical command).

## 19. Accepted pilot risks (explicit)

1. **Single-instance pilot topology.** The pilot runs one backend pod with `CELERY_TASK_ALWAYS_EAGER=1`. Scale-out is not a pilot requirement; the registry fan-out (§20) is pre-public-rollout work. Bounded by the pilot's invite-only scale.
2. **Tally → TaxMind voucher auto-import is out of scope.** Pilot users create vouchers in TaxMind (mobile/web), which sync to Tally. Reverse voucher ingestion is a future phase, by design.
3. **On-device mobile end-to-end is operator-walked, not automated.** The mobile suite is green; the pilot operator performs the first on-device walkthrough (§18).
4. **Cosmetic close-code and `tally_version` null.** Both are display-level; rejection and dispatch behavior are correct.
5. **Dashboard UTC/IST off-by-one.** Accepted for the pilot window; the operator is aware and reads reports with explicit `as_of_date` parameters during the affected window.

None of the above is a pilot blocker. Each is bounded by the controlled scope or scheduled for pre-public-rollout remediation.

## 20. Deferred engineering backlog (pre-public-rollout, not pilot-blocking)

- **BUG-Books-003 fix:** Redis pub/sub fan-out for the `connector_registry` so multi-instance backend deploys work without `task_always_eager`. (Single-instance is correct for the pilot.)
- **Mobile end-to-end on-device validation (§7.6 full):** Expo render + connector-enroll-from-app + manual-voucher-entry-from-app on a real device.
- **Timezone-aware date boundaries:** company-level `timezone` column (default `Asia/Kolkata`); rewrite `dashboard_service` + reports endpoint defaults to compute day boundaries in that zone.
- **Real notification providers:** FCM + APNs HTTP/2 clients; real email (SES/Postmark) for account-lifecycle.
- **Data-export-on-delete:** populate `account_deletion_requests.final_export_s3_key` before the grace period ends.
- **`first_invoice_extracted` checklist item:** wire to the Phase-1+ `ingestions` table.
- **Celery beat schedule** for `process_due_account_deletions` (daily).
- **Connector auto-update / Windows Service for Gold-on-server deployments** (Phase 5; out of pilot scope).
- **`audit_logs.user_id` cascade vs append-only trigger:** pick one of the two documented paths in `docs/AUDIT.md` before any future `DELETE FROM users` code.
- **Live-Tally integration tests with deliberate failure injection** per connector command (the Phase 1 gating policy in `PHASE_0_CLOSEOUT.md`): Tally down, no company loaded, wrong company loaded, network drop mid-post, connector offline at dispatch time — assert each failure is *visible to the operator*.

## 21. Founder / operator actions

To start and run the controlled pilot:

1. **Invite only consented pilot users.** Confirm each participant in writing; no public signup link.
2. **Confirm the pilot Tally fixture.** The live validation used the dedicated test fixture company ("Taxmind Books" ledgers: ABC LTD / Cash / HDFC BANK / Profit & Loss A/c / Xyz Ltd). Do not point pilot connectors at production client books without explicit consent.
3. **Run the pilot on the single-instance topology** (the deployed VPS already runs `CELERY_TASK_ALWAYS_EAGER` per the deployment runbook). Do not scale to a second backend pod until the §20 registry fan-out lands.
4. **Keep monitoring on:** VPS health probes, backup timer + restore spot-checks, `/health`, `/connector/status`, and audit-log sampling.
5. **Walk the mobile end-to-end on a device** before declaring the pilot fully exercised (§7.6 full — operator-owned).
6. **During the 00:00–05:29 IST window,** read dashboard with an explicit date or rely on reports endpoints; note the UTC/IST caveat until the §20 timezone fix lands.
7. **Do not declare "production ready"** at the end of the pilot. Re-open this readiness document and re-decide against the §20 backlog before any unrestricted rollout.

## 22. Exact versions / commits

| Component | Version | Evidence |
|---|---|---|
| TaxMind backend | commit `c222e610c0eed2f452667f4f279e3d641b41940e` (`c222e61`) | `git rev-parse HEAD` at validation |
| Backend `APP_ENV` | `development` (local validation) / production env on VPS per runbook | `backend/.env` |
| Alembic head (local) | **0011** | `alembic_version` table |
| Connector (running from source) | `connector_version=0.1.0`, `connector_build_sha=803921c` (built 2026-07-21) | `GET /connector/status`; `connector/dist/BUILD_INFO.json` |
| Mobile | TypeScript clean, 35 Jest tests (10 suites) | `npm test` |
| TallyPrime | running on the validation host (`C:\Program Files\TallyPrime\tally.exe`, PID 8072); port 9000 IPv4 open; `tally_version` not surfaced by connector (reports `null`) | live probe + process inspection |
| Tally company loaded | dedicated **test fixture** "Taxmind Books" (ledgers ABC LTD / Cash / HDFC BANK / Profit & Loss A/c / Xyz Ltd; GUID prefix `ed86199b-…`) | read-only Tally probe |

**Phase 0 provenance** (from `docs/PHASE_0_CLOSEOUT.md`): Phase 0 closed 2026-05-16 at `f0c5fc0` (46 numbered tasks + 10 cross-cutting = 56 commits); post-validation patches P0.46b/c/d, P0.58, BUG-Books-004 Layer A fix (`4832688`), BUG-Books-005 fix (`ec68199`) all shipped on `main`. The validation host is at `c222e61` (ahead of the Phase 0 closeout SHA; mobile dashboard work landed since).

## 23. Evidence paths

**Live Tally validation (this session):** all artifacts under `C:\Users\GAURAV\AppData\Local\Temp\opencode\` (outside the repo, by design — no tracked files modified):
- `tally_probe_fresh.py` — read-only Tally ledger/company probe (confirmed the test fixture before any write)
- `voucher_probe.py`, `voucher_probe2.py` — read-only Tally Day Book export (confirmed vouchers #10 and #11)
- `reset_pw.py` — dev-test owner password reset (backend venv)
- `access_token.txt`, `new_connector_token.txt`, `enroll_resp.json`, `dispatch_resp.json`, `replay_resp.json`, `stranded_resp.json`, `status_resp.json`, `sync_resp.json`, `ws_wrong.txt`, `cross_v.json`, `cross_s.json`, `crosspost.json`, `v_own.json`, `v_wrong.json`, `poll_*.json`, `rcpoll_*.json`
- DB rows (test data only, local `taxmind_books`): vouchers `a3ba7c4d-0d2a-49f0-8c11-b9f91772a31d`, `82a63649-d33a-420b-8264-d0c34e01897f`; connector `003e96c1-419d-47de-a3e1-fff18b9d911f`; 5 ledger upserts.

**In-repo evidence:**
- `docs/PHASE_0_CLOSEOUT.md` — operational closeout, task ledger, known issues, deferred items.
- `docs/VALIDATION_REPORT.md` — §7.5a/§7.5b/§7.6 checklist + notes (§7.5 COMPLETE 2026-07-21; §7.5b rejection-lane 2026-05-22; §7.5a 2026-05-18).
- `docs/connectOR_PROTOCOL.md` — frozen v1 connector contract.
- `CLAUDE.md` — enrollment ceremony + `psql` access notes.
- `validation/` — prior session logs, probe XML, fixtures, `spot_check_7_5a.py`, phase reports.

**VPS / automated:** CI runs (backend 605 / connector 103 / mobile 35 / tenant-isolation 15) and the VPS health soak log from the deployment session.

## 24. Final recommendation

Proceed to a **controlled, invite-only pilot** under the conditions stated at the top of this document. All live integration gates the pilot depends on are **VERIFIED PASS**; the open items are either **ACCEPTED PILOT RISK** (bounded by the controlled scope) or **DEFERRED PRE-PRODUCTION WORK** (scheduled before any unrestricted rollout). The production VPS was not modified during local Tally validation; no real client data was used; the live Tally validation used the dedicated test fixture.

Re-open this document and re-decide before any move beyond the controlled pilot. **Production readiness is a separate, future gate.**

---

## Appendix: Validation hygiene (explicit)

- The **production VPS was not modified** during local Tally validation. All VPS-status facts above (§§2–7, §17) are from the prior deployment/security-review session and were re-confirmed read-only.
- **No tracked files were modified** during validation. `git status` after the session is identical to before (pre-existing dirty entries only; no new repo writes by the validator).
- **No commits, pushes, merges, tags, or deployments** occurred during validation.
- The **only database writes** during live validation were **TEST DATA** in the **local development database** (`taxmind_books` on `localhost:5432`): a dev-test owner password reset, one enrollment code + connector row, five idempotent ledger upserts, two test vouchers, and their audit rows.
- **Tally validation used the dedicated test fixture** company ("Taxmind Books" ledgers: ABC LTD / Cash / HDFC BANK / Profit & Loss A/c / Xyz Ltd), confirmed by a read-only Tally probe before any write. **No real client accounting data was used.**
- **Authentication and security controls were not disabled** at any point. The tenant-isolation checks specifically exercised the live auth/authorization path (403/404 rejections observed).

---

**END OF DECISION DOCUMENT.** No remediation started. This document is a decision record, not an instruction to change code.

---

# ADDENDUM A — P3.7 Phase 7C: real-company ledger master sync — **PASS / CLOSED**

**Recorded:** 2026-08-18. **Additive milestone entry.** The controlled-pilot decision above (§§1–24) is unchanged and remains the 2026-08-12 record; this addendum documents a later, independently-gated milestone and does **not** modify any prior result, PASS/FAIL, or number.

**Scope.** First **real-company** (not the "Taxmind Books" test fixture) Tally **ledger master** sync persisted into the **production** database, executed through the production code path behind the fail-closed mapping gate. Gate-by-gate result below. **No vouchers, no company creation, no mapping changes, no schema/migration, no repository changes.**

## A.0 Phase 7C decision — VERIFIED PASS

| Gate | Subject | Result |
|---|---|---|
| Gate 1 | Company mapping | **VERIFIED PASS** |
| Gate 3 | Live Tally identity | **VERIFIED PASS** |
| Gate 4 | Fresh dry run | **VERIFIED PASS** |
| Gate 5 | Pre-write safety | **VERIFIED PASS** |
| Gate 6 | Persistence | **VERIFIED PASS** |
| Gate 7 | Post-persist verification | **VERIFIED PASS** |
| Gate 8 | Idempotency | **VERIFIED PASS** |
| Gates 9–10 | Regression / safety | **VERIFIED PASS** |

### Gate 1 — Company mapping
- Production DB holds **exactly one** company with a Tally GUID: Tally GUID `c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9` mapped to company `32a51be2-13f5-4b75-a67e-0f1d77b3121f` — **Vighnaharta Agro Chemicals**.
- Audit event `company.tally_mapping_configured` is present.
- **GURUDEV ENGINEERS** has no Tally mapping. No cross-mapping exists.

### Gate 3 — Live Tally identity
- Local TallyPrime running on HTTP `:9000`; open company **"Vighnaharta Agro Chemicals - FROM 1-APR-2025"**.
- Live Tally company GUID **exactly matches** the production mapping: `c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9`.
- All **623** ledger GUIDs carry the correct company-GUID prefix.

### Gate 4 — Fresh dry run
- Mapping reconfirmed against the production DB. `status=safe`, `method=guid`.
- **623** total · **623** valid GUIDs · **0** missing · **0** duplicates · **0** malformed · **0** manual-review · **0** conflicts · **623** new candidates · **0** existing matches.

### Gate 5 — Pre-write safety review
- Baseline intact: `companies=2`, `ledgers=0`, `vouchers=0`.
- Target company mapping intact; **0** cross-company GUID collisions.
- Unique indexes present: `uq_ledgers_company_tally`, `uq_ledgers_company_name`.

### Gate 6 — Persistence
- Persisted through the production path: `persist_sync_masters_payload` → `LedgerService.upsert_from_sync` → fail-closed mapping gate.
- **Created: 621 · Updated: 0 · Skipped: 2.**
- The **2 skipped** records are legitimate intra-batch **duplicate display names with distinct GUIDs/GSTINs** — **BALIRAJA KRUSHI KENDRA** and **SIYARA INDUSTRIES** — correctly **not merged or overwritten** (Case C duplicate-name handling).

### Gate 7 — Post-persist verification
- `ledgers=621`, all company-scoped: `other_companies=0`, `no_guid=0`, `dup_guid=0`, `nonzero-opening=0`, `inactive=0`, `synced=621`.
- Audit: `ledger.created=621`, `ledger.updated=0`.
- **No** vouchers created · **no** company created · **no** mapping changes.

### Gate 8 — Idempotency
- Identical re-sync: **created=0 · updated=0 · skipped=2** (the same two legitimate name/GUID conflicts).
- Row count **621 → 621**; no duplicate rows; no phantom audit events.

### Gates 9–10 — Regression / safety
- Backend pytest: **705 passed**. Connector pytest: **135 passed**. **Ruff clean** for both.
- **GURUDEV ENGINEERS** remains `ledgers=0` and **untouched**.
- Cross-company GUID reuse: **0**. Vouchers: **0**.
- Case D hard-stop present at `ledger_service.py:347`.
- Production API: `{"status":"ok","env":"production"}`.

## A.1 Critical safety result (explicit)
- **Vighnaharta Agro Chemicals:** 621 ledgers persisted.
- **GURUDEV ENGINEERS:** 0 ledgers, untouched.
- **Cross-company GUID collisions:** 0.

## A.2 Final production state
- Production DB: `companies=2`, `ledgers=621`, `vouchers=0`.
- Vighnaharta Agro Chemicals fully synced with **621** ledger masters; the other company untouched.
- No repository changes were required for this verification; throwaway VPS files were cleaned.

## A.3 Status
**P3.7 Phase 7C — PASS / CLOSED.** Phase 7C is no longer pending. This milestone does **not** declare full production readiness; the next phase remains governed by the existing roadmap and review gates recorded above (§20 deferred pre-production backlog; §24 "production readiness is a separate, future gate"). No next phase is introduced here.

---

# ADDENDUM B — P3.8: multi-company Tally integration — **IMPLEMENTATION COMPLETE / PRODUCTION DEPLOYMENT BLOCKED**

**Recorded:** 2026-08-21. **Additive milestone entry.** Addenda A and §§1–24 above are unchanged and remain their own dated records; this addendum documents a later, independently-gated milestone and does **not** modify any prior result, PASS/FAIL, or number.

**Scope.** P3.8 adds multi-company Tally discovery and routing: a connector can discover multiple Tally companies on one installation, the backend maps them via a trusted discovery reference, and both API routing and connector routing become company-scoped with installation-scoped connector authorization. This addendum records what is **verified in the repository/CI (non-production)** and draws an explicit line against what remains **unverified in production**.

## B.0 Release identity — VERIFIED

| Field | Value |
|---|---|
| Release SHA | `ae6fa6606112a18e3884423445cbd74469db7094` |
| Release tag | `v3.8.0` |
| `main` | `= origin/main = ae6fa6606112a18e3884423445cbd74469db7094` |

P3.8 commits (verified present in this exact order on `main`):
```
5c84fa6  feat(p3.8): add multi-company tally integration
20b84ea  fix(p3.8): complete connector discovery validation
595e5c5  build(mobile): add EAS production profile
ae6fa66  test(connector): make active-company poll test deterministic
```

## B.1 Non-production verification — VERIFIED

| Check | Result |
|---|---|
| Backend tests | **710 passed** |
| Connector tests | **143 passed** |
| Mobile tests | **42 passed** |
| Mobile type-check | **PASS** |
| Ruff | **PASS** |
| CI | **GREEN** |
| Connector build | **SUCCESS** |

These results are recorded as the authoritative non-production verification for this release; they were not re-executed as part of this documentation update.

## B.2 P3.8 implementation — VERIFIED (present in the tagged release)

- Multi-company Tally discovery.
- Trusted discovery-reference mapping.
- Multiple-company routing (API) and multiple-connector routing.
- Active-company switching.
- Mobile company-context invalidation.
- Connector discovery.
- Installation-scoped connector authorization.
- Migrations **0016** and **0017** (chain confirmed in-repo: `0015_company_tally_master_id.py → 0016_p38_connector_discovery.py → 0017_p38_authoritative_discovery_ids.py`). Both are additive and reversible.
- Tenant isolation / security controls.

## B.3 Connector and mobile production targets — VERIFIED (configuration, not deployment)

- Connector production WS default (confirmed in `connector/connector/config.py`): `wss://books.gcwealthguru.com/api/v1/connector/ws`. (Supersedes the stale `wss://api.taxmindbooks.com/...` placeholder noted in earlier project history — that domain does not resolve and is no longer the configured default.)
- Mobile: a **production** EAS profile exists (confirmed in `mobile/eas.json`) targeting `EXPO_PUBLIC_API_BASE_URL=https://books.gcwealthguru.com`.
- Neither of the above is evidence of a production deployment or a production build having been *run* — see §B.4.

## B.4 Production deployment gate

**P3.8 PRODUCTION DEPLOYMENT IS NOT VERIFIED COMPLETE.** The remaining blocker is **authorized VPS deployment access**. Status: **READY FOR PRODUCTION DEPLOYMENT / BLOCKED ON AUTHORIZED VPS ACCESS** — consistent with this document's existing "VERIFIED PASS / ACCEPTED PILOT RISK / DEFERRED PRE-PRODUCTION WORK / NOT TESTED-OUT OF SCOPE" classification convention (see the header note above §1).

Remaining production sequence, with explicit status per step:

| # | Step | Status |
|---|---|---|
| 1 | Obtain authorized VPS/deployment access | **BLOCKED — VPS ACCESS** |
| 2 | Verify production source and deployment configuration | **NOT STARTED** |
| 3 | Verify current Alembic revision is `0015` | **NOT STARTED** |
| 4 | Take and verify a production `pg_dump` backup | **NOT STARTED** |
| 5 | Deploy exact release `v3.8.0` / `ae6fa6606112a18e3884423445cbd74469db7094` | **NOT STARTED** |
| 6 | Run on the host tree: `alembic upgrade head` | **NOT STARTED** |
| 7 | Verify Alembic = `0017` | **NOT STARTED** |
| 8 | Rebuild/recreate production backend | **NOT STARTED** |
| 9 | Verify health/API/WebSocket | **NOT STARTED** |
| 10 | Deploy/verify P3.8 connector | **NOT STARTED** |
| 11 | Build production mobile application (`eas build --profile production`) | **NOT STARTED** |
| 12 | Perform controlled multi-company E2E verification | **NOT STARTED** |
| 13 | Verify no unexpected company/ledger/voucher changes | **NOT STARTED** |
| 14 | Close the production gate | **NOT STARTED** |

All fourteen steps are gated behind step 1. None of steps 2–14 has been attempted; none is claimed as verified. This document does **not** state that P3.8 was deployed to production, that migration `0017` was applied in production, that the production backend was rebuilt, that the production connector was deployed, that a production EAS build was completed, or that a production multi-company end-to-end test was completed — none of those claims is supported by repository evidence as of this recording.

## B.5 Data safety — explicit confirmation

As of the latest verified checkpoint (this recording, 2026-08-21):

- **No** P3.8 production migration was performed.
- **No** production company was created.
- **No** production company mapping was performed as part of P3.8 deployment.
- **No** production ledger synchronization was performed as part of P3.8 deployment.
- **No** production voucher synchronization was performed as part of P3.8 deployment.
- **No** production EAS build was performed.

No before/after production row counts are recorded for P3.8, because no production mutation occurred to count. (This is distinct from Addendum A's Phase 7C figures, which describe an earlier, separately-gated production milestone and are not restated or altered here.)

## B.6 Status
**P3.8 implementation — COMPLETE and merged to `main` at `v3.8.0` (`ae6fa66`), with non-production verification (tests/CI/build) PASSING.** **P3.8 production deployment — NOT VERIFIED COMPLETE, BLOCKED on authorized VPS access** (§B.4). This addendum does not close the production gate and does not declare full production readiness; the next action is step 1 of §B.4, after which steps 2–14 govern closing the gate. No next phase beyond the production gate is introduced here.

---

# ADDENDUM C — P3.8 live E2E verification — **PASS / CLOSED**

**Recorded:** 2026-08-24. **Additive milestone entry.** Addenda A, B, and §§1–24 above are unchanged and remain their own dated records; this addendum documents a later, independently-gated milestone and does not modify any prior result, PASS/FAIL, or number.

**Scope.** Full live E2E verification of P3.8 multi-company Tally integration against a real TallyPrime 7.x installation. Includes two connector bug fixes discovered during E2E and a live active-company switch test.

## C.0 Release identity

| Field | Value |
|---|---|
| Final release SHA | `4f0928b98b00cf43a15445ef76267249faf94e7d` |
| Branch | `main` |
| Ahead of origin | 2 commits (the two P3.8 bug fixes) |

**P3.8 commits (complete list on `main`):**
```
4f0928b  fix(connector): strip Tally control chars from GUID in UTF-16 extraction  (NEW)
2317f52  fix(connector): fix Tally company discovery for TallyPrime 7.x            (NEW)
ae6fa66  test(connector): make active-company poll test deterministic
595e5c5  build(mobile): add EAS production profile
20b84ea  fix(p3.8): complete connector discovery validation
5c84fa6  feat(p3.8): add multi-company tally integration
```

## C.1 Bugs fixed during E2E

### Bug 1: CMPINFO XPath collision (`tally_client.py`)
**Problem:** `root.find(".//COMPANY")` matched `<COMPANY>0</COMPANY>` counter in CMPINFO instead of actual company data in `DATA/COLLECTION/COMPANY`. Active company name and GUID always returned empty/None.
**Fix:** Changed XPath to `.//DATA/COLLECTION/COMPANY`.
**Commit:** `2317f52`

### Bug 2: TallyPrime 7.x binary format (`tally_data_folder.py`)
**Problem:** `Manager.500` files don't exist in TallyPrime 7.x (uses `Manager.1800` binary + `Company.1800` UTF-16-LE). Data folder discovery returned 0 companies.
**Fix:** Added second extraction strategy that reads `Company.1800` UTF-16-LE binary files.
**Commit:** `2317f52`

### Bug 3: Tally control character GUID prefix (`tally_data_folder.py`)
**Problem:** TallyPrime prefixes GUIDs with control characters (e.g. `J`, `Z`) in Company.1800. GUID regex anchored at position 0 failed on `Jed86199b-...`. Company 100000 had no clean GUID copy, so active company identifier resolved as null.
**Fix:** Strip leading non-hex characters before matching the GUID pattern.
**Commit:** `4f0928b`

## C.2 Live E2E verification — all PASS

| Domain | Verdict | Evidence |
|---|---|---|
| Authentication | ✅ PASS | Register 201, Login 200, JWT valid |
| Backend | ✅ PASS | `GET /health` → `{"status":"ok"}` |
| Connector | ✅ PASS | `connected=true, tally_running=true, build_sha=ae6fa66` |
| Tally gateway | ✅ PASS | `GET :9000` → 200, TallyPrime Server Running |
| Discovery | ✅ PASS | 2 companies discovered: 100000 + 100010 |
| Trusted mapping | ✅ PASS | 100010 → backend company via discovery_id |
| Multi-company routing | ✅ PASS | 100010 mapped, 100000 unmapped |
| **Active-company switching** | ✅ **PASS** | Live 100010→100000 switch verified |
| Tenant isolation | ✅ PASS | Unauthorized→404, Missing header→422 |
| Data safety | ✅ PASS | 0 ledgers, 0 vouchers created |
| Tests (116/116) | ✅ PASS | Backend 13, Connector 61, Mobile 42 |

## C.3 Active-company switch evidence

**Pre-switch:** Active company = "Vighnaharta Agro Chemicals" (100010, GUID `c30a0ee5-...`)

**User switched Tally from 100010 → 100000.**

**Post-switch triple-match:**
- Tally XML API: `name="Taxmind Books Test", guid="ed86199b-..."`
- Backend `active-tally-company`: `identifier="100000", master_id="ed86199b-..."`
- Discovery record 100000: `tally_master_id="ed86199b-..."`

All three agree. Connector polling detected the change within 10s. `tally_company_changed` event emitted and processed by backend with correct `previous: '100010'`, `new: '100000'`.

## C.4 Test results

| Suite | Count | Status |
|---|---|---|
| Backend (`test_connector_registry` + `test_connector_discovery_p38`) | 13 | ✅ 13/13 |
| Connector (`test_tally_data_folder` + `test_message_handlers` + `test_tally_client`) | 61 | ✅ 61/61 |
| Mobile (12 suites) | 42 | ✅ 42/42 |
| Ruff | — | ✅ Clean |
| **TOTAL** | **116** | **✅ 116/116** |

## C.5 Migration status

| Migration | Status |
|---|---|
| 0016 (`p38_connector_discovery`) | ✅ Applied |
| 0017 (`p38_authoritative_discovery_ids`) | ✅ Applied |

Both additive and reversible. Schema changes: `connectors`, `connector_company_bindings`, `tally_companies_discovered` tables; new `voucher_status` enum values.

## C.6 Connector artifact

| Field | Value |
|---|---|
| Source SHA (all fixes) | `4f0928b` |
| `.exe` artifact SHA | `ae6fa66` (pre-fix, needs rebuild before production) |
| E2E tested from | Source checkout (`.venv/Scripts/python`) |
| Production action | Rebuild `.exe` from `4f0928b` before deployment |

## C.7 Production deployment status

**NOT DEPLOYED.** Production deployment remains blocked on authorized VPS access per §B.4. The live E2E was performed against a local development environment with a real TallyPrime installation.

## C.8 Remaining LOW limitations

| Item | Severity | Notes |
|---|---|---|
| Mobile device test | LOW | Code-level verification passed; no emulator/device available |
| Voucher queue-on-mismatch | LOW | P0.53 flow not exercised (no vouchers created) |
| 30-day queue expiry | LOW | P0.54 beat task not exercised |
| Reverse company switch | LOW | One-direction switch (100010→100000) verified; reverse not tested |
| `.exe` rebuild | LOW | Artifact at `ae6fa66`; needs rebuild from `4f0928b` for production |

None of the above is a release blocker for P3.8 functional E2E closure.

## C.9 Status

**P3.8 live E2E verification — PASS / CLOSED.** The functional E2E is complete. All multi-company discovery, routing, mapping, and active-company switching flows are verified with live runtime evidence against a real TallyPrime 7.x installation. Production deployment remains gated on VPS access (§B.4); the connector `.exe` needs rebuilding from the final source SHA. This addendum closes the P3.8 functional verification gate; it does not close the production deployment gate.

---

# ADDENDUM D — local connector launch-directory fix + production health check — **NON-CLOSING**

**Recorded:** 2026-09-03. **Additive milestone entry.** Addenda A, B, C and §§1–24 above are unchanged and remain their own dated records; this addendum does not modify any prior result, PASS/FAIL, or number. **This addendum does not close, advance, or alter the §B.4 production deployment gate.** All fourteen §B.4 steps remain exactly as recorded in Addendum B: step 1 blocked on authorized VPS access, steps 2–14 NOT STARTED. Addendum C §C.7 ("NOT DEPLOYED") is likewise unchanged.

## D.0 What was verified this session

| Check | Result | Evidence |
|---|---|---|
| Production health endpoint (read-only, unauthenticated) | **VERIFIED PASS** | `GET https://books.gcwealthguru.com/health` → HTTP 200, `{"status":"ok","env":"production"}` |
| Local dev connector launch-directory root cause | **VERIFIED** | See D.1 |
| Local dev connector launch-directory fix | **VERIFIED PASS (local only)** | Connector relaunched with corrected working directory; `--version` reports build `1af8bc3`; process stayed alive past the `CONNECTOR_TOKEN`/`CONNECTOR_COMPANY_ID` guard checks in `_async_main`; repeated `POST http://localhost:9000 → 200 OK` observed in connector stderr against the local Tally instance |

**Explicit scope limit on the health-endpoint check:** this confirms *a* backend process is live and responding at `books.gcwealthguru.com` under `env=production`. It does **not** identify which commit/release is currently deployed there, and does **not** confirm migrations `0016`/`0017` have been applied against the production database. It is one data point, not a substitute for §B.4 steps 2–9.

## D.1 Local connector launch-directory issue — root cause and fix

**Symptom:** `TaxMindBooksConnector.exe --version` correctly reported build `1af8bc3`, but launching the connector normally exited with `CONNECTOR_TOKEN missing — run the enrollment flow first.`, despite a valid token being present in `connector/dist/.env`.

**Root cause:** `ConnectorSettings.model_config` in `connector/connector/config.py` sets `env_file=".env"` — a relative path. `pydantic-settings` resolves relative `env_file` paths against the process's current working directory at launch, not the executable's own directory; PyInstaller does not change this. The connector had been launched with CWD = the repository root, where an unrelated backend `.env` template (`docs`/env-example style, containing `TALLY_HOST`/`TALLY_PORT` but no `CONNECTOR_TOKEN` key) was picked up instead of `connector/dist/.env`, so `CONNECTOR_TOKEN` resolved to its Pydantic field default (`None`).

**Fix:** operational only — launch the executable with its working directory set to `connector/dist` (e.g. `Start-Process ... -WorkingDirectory "…\connector\dist"`, or `cd` into that directory before running). **No source, config-loading, or packaging code was changed.**

**Verification performed:** re-launched with corrected CWD; `--version` still reports `1af8bc3`; process ran without the missing-token exit; connector's own stderr showed successful periodic `POST` calls to the local Tally XML interface (`http://localhost:9000` → `200 OK`), consistent with the connector's local Tally-discovery/active-company polling succeeding.

**Verification NOT performed (and not claimed):** the connector's WebSocket handshake to the backend was not independently confirmed in this session — a successful WS connection produces no log output by design (per prior project history), and this session had no backend access token to call `GET /api/v1/connector/status`. `connected=true` is therefore **not asserted** here. Tenant isolation, active-company switching, discovery-record correctness, and absence of unintended mutation were **not re-exercised** this session; those remain as last recorded in Addendum C (§C.2–§C.3, dated 2026-08-24, against a local development environment — not production).

## D.2 Explicit non-claims

This addendum does **not** state, and no evidence in this session supports, that:
- P3.8 migrations `0016`/`0017` were applied to the production database.
- A production database backup was taken or verified as part of this session.
- The production connector was enrolled, launched, or reached `connected=true`/`tally_running=true` against the production backend.
- Tenant isolation, discovery, or active-company switching were verified against production.
- Any of the §B.4 steps 2–14 advanced.

## D.3 Status

**Addendum D is informational and non-closing.** It records a local development environment fix (connector launch working directory) and one read-only production health-endpoint check. **The P3.8 production deployment gate (§B.4) and production deployment status (§C.7) are unchanged: production deployment remains NOT VERIFIED COMPLETE, blocked on authorized VPS access.** No release verdict is issued by this addendum.

---

# ADDENDUM E — P3.8 production deployment reconciliation — **RELEASED / VERIFIED (backend + connector); two named items remain open**

**Recorded:** 2026-09-03. **Additive milestone entry.** Addenda A, B, C, D and §§1–24 above are unchanged and remain their own dated records; nothing above is edited, deleted, or renumbered. This addendum is a **documentation-only reconciliation session** — no application/source code was modified, and no production system was accessed, queried, or changed by Claude during the writing of this addendum.

## E.0 Nature of the evidence in this addendum (read this first)

This addendum draws on three distinct evidence classes. They are kept separate throughout rather than merged into one undifferentiated "VERIFIED PASS," because they carry different evidentiary weight:

| Class | What it means | How it's marked below |
|---|---|---|
| **(i) Repo-checked, this session** | Claude independently ran a read-only check against this git repository during this documentation session and reports the actual result. | "**Repo-verified**" |
| **(ii) Operator-reported, this session** | The operator (Gaurav) reported a production runtime result in conversation. Claude did not execute the underlying command, did not observe raw tool output, and has no VPS/production access in this session to independently re-run it. | "**Operator-reported**" |
| **(iii) Historical record** | Already recorded, with its own evidence, in Addenda A–D or §§1–24 above. | "**Per [addendum/section]**" |

Per instruction, this addendum does not fabricate command output, timestamps, backup paths, or HTTP bodies beyond what was actually supplied, and does not claim Claude performed production actions it did not perform.

## E.1 Repo-verified checks (run this session)

| Check | Result |
|---|---|
| Full SHA of `1af8bc3` | **Confirmed**: `git rev-parse 1af8bc3` → `1af8bc3b645a5456992711aca3c0377765233b53`, matching the reported final source SHA exactly. |
| What `1af8bc3` is | It is `docs(release): add P3.8 E2E closure addendum to readiness doc` — i.e. the commit that added Addendum C to this document. The last *functional* commit on `main` before it is `4f0928b` (`fix(connector): strip Tally control chars…`), already recorded in Addendum C as the "Final release SHA" for the functional fixes. |
| `v3.8.0` tag target | **Discrepancy found:** the local `v3.8.0` tag object still resolves to `ae6fa6606112a18e3884423445cbd74469db7094` (the SHA recorded in Addendum B), **not** to `1af8bc3` or `4f0928b`. The tag was not moved forward when the two post-tag connector fixes (`2317f52`, `4f0928b`) and the Addendum C/D docs commits landed on `main`. This is a pre-existing bookkeeping gap, not something this session introduced — flagged here so "release SHA" and "tag" aren't read as interchangeable. |
| Alembic migration files `0015`→`0017` | **Present in-repo**: `0015_company_tally_master_id.py`, `0016_p38_connector_discovery.py`, `0017_p38_authoritative_discovery_ids.py` — chain matches what Addenda B/C already recorded. This confirms the *migration files exist and are the ones described*; it says nothing about whether they were applied to the production database (see §E.2). |
| `HEAD` at time of this reconciliation | `558c3a3018f47e520c6fa059ead7c069242e254f` (`docs(release): add P3.8 addendum D`) — i.e. `1af8bc3` is one commit behind current `HEAD`, and both are docs commits. |

## E.2 Operator-reported production evidence (this session)

The operator reported the following about the actual production deployment. None of it was independently re-executed by Claude in this session (no VPS/SSH/database/connector access was available or used):

| Area | Operator-reported result |
|---|---|
| Health endpoint | `GET https://books.gcwealthguru.com/health` → HTTP 200, `{"status":"ok","env":"production"}` |
| Migrations | `0015 → 0016 → 0017` applied to the production database |
| Backup | A production backup was taken and restore-verified |
| Backend deploy | Production backend rebuilt/deployed from the approved release |
| P3.8 routes | Verified live in production |
| Connector enrollment | Enrollment ceremony completed; connector token obtained and configured in `connector/dist/.env` (working directory required per Addendum D §D.1 — operational finding, not a code change) |
| Connector status | `connected=true, tally_running=true` against the production backend |
| Tally gateway | `GET :9000` → HTTP 200, `<RESPONSE>TallyPrime Server is Running</RESPONSE>` |
| Discovery / active-company | Discovery and active-company flow exercised against production |
| Tenant isolation | Referenced as previously verified (Addendum C, local dev) |
| Data safety | No unintended company/mapping/ledger/voucher activity during deployment/verification |
| Tests | Backend 710 / Connector 143 / Mobile 42 passed; mobile type-check PASS; Ruff PASS; CI GREEN — these are the **same figures already on record in Addendum B §B.1**, not a new test run reported for this session |

## E.3 Reconciling against the §B.4 fourteen-step production gate

| # | §B.4 step | Status after this reconciliation | Basis |
|---|---|---|---|
| 1 | Authorized VPS/deployment access | **CLOSED** | Operator-reported; implied by all subsequent steps |
| 2 | Verify production source/config | **CLOSED** | Operator-reported ("rebuilt/deployed from the approved release") |
| 3 | Verify pre-migration Alembic = `0015` | **CLOSED (inferred)** | Operator-reported migration chain starts at `0015`; no explicit pre-check output supplied |
| 4 | Take/verify production `pg_dump` backup | **CLOSED, with a caveat** | Operator-reported "previously taken" — the phrasing doesn't distinguish a fresh pre-migration snapshot from the standing backup capability already recorded at §6/Addendum A; operator should confirm a dedicated pre-`0016` snapshot exists if one doesn't already |
| 5 | Deploy exact release `v3.8.0` / SHA | **CLOSED, with the tag caveat in §E.1** | Operator-reported; SHA `1af8bc3` repo-verified; `v3.8.0` tag object itself is stale (points to `ae6fa66`) — recommend re-tagging if the tag is meant to track the deployed source |
| 6 | Run `alembic upgrade head` | **CLOSED** | Operator-reported |
| 7 | Verify Alembic = `0017` | **CLOSED** | Operator-reported |
| 8 | Rebuild/recreate production backend | **CLOSED** | Operator-reported |
| 9 | Verify health/API/WebSocket | **CLOSED** | Health: operator-reported HTTP 200 body. WebSocket: inferred from connector `connected=true`, not from a raw WS-upgrade check |
| 10 | Deploy/verify P3.8 connector | **CLOSED** | Operator-reported |
| 11 | Build production mobile application (`eas build --profile production`) | **NOT CLOSED — open item** | Nothing in this session's evidence describes an EAS production build being run. The "Mobile: 42 passed" figure is the unit-test count already on record (Addendum B), not a build. This step remains as last recorded: **NOT STARTED**. |
| 12 | Controlled multi-company E2E verification (production) | **NOT CLOSED — open item** | The evidence states discovery/active-company was "exercised" against production, but active-company *switching* is explicitly reported as "previously verified" — i.e. referring back to Addendum C's local-dev-environment test, not a fresh switch test against production. A full multi-company switch-and-verify E2E specifically against the production environment is not evidenced here. |
| 13 | Verify no unexpected company/ledger/voucher changes | **CLOSED (for the deployment window)** | Operator-reported; scoped to "during deployment/verification," not a substitute for the full E2E in #12 |
| 14 | Close the production gate | **PARTIALLY CLOSED** | See §E.4 — the gate closes for backend + connector; items 11 and 12 remain open |

## E.4 Final verdict

**P3.8 backend + connector production deployment: RELEASED / VERIFIED**, on the basis of the operator-reported evidence in §E.2 reconciled against the §B.4 gate in §E.3, with the SHA/tag caveat in §E.1.

**Two items are explicitly NOT closed by this reconciliation and remain open, not fabricated as done:**
1. **Mobile production build** (§B.4 step 11) — no EAS production build is evidenced.
2. **Fresh multi-company switch E2E against the production environment** (§B.4 step 12) — the only switch test on record remains Addendum C's, against a local development environment.

This addendum does **not** claim Claude independently verified the production runtime facts in §E.2 — those are recorded as operator-reported, per §E.0. It does independently confirm, from the repository itself, the SHA identity and migration-file chain in §E.1, and surfaces the pre-existing `v3.8.0` tag/SHA mismatch for the operator's awareness.

## E.5 P3.8 freeze

**P3.8 IS NOW FROZEN.** No further P3.8 engineering changes should be made unless a new defect, security issue, or separately approved change is identified. Items 11 and 12 above, if pursued, should be tracked as new, separately-scoped follow-up work against this frozen baseline — not as unfinished P3.8 engineering.

## E.6 Status

**Addendum E closes the P3.8 production deployment gate for backend + connector scope.** Mobile production build and a fresh production-environment multi-company switch test remain open items, explicitly not claimed as done. Production readiness beyond the controlled pilot remains a separate, future gate per §1/§24.

---

# ADDENDUM F — P3.9: production multi-company E2E — **ITEM 12 OPEN / NOT VERIFIED (operational blocker recorded)**

**Recorded:** 2026-09-03. **Additive milestone entry.** Addenda A–E and §§1–24 above are unchanged and remain their own dated records; nothing above is edited, deleted, or renumbered. This addendum is a **documentation-only record of an operational blocker** — no application/source code was modified, no authentication/authorization change was made, no production system was modified, and no evidence was fabricated.

## F.0 Recording basis and evidence classes

Same evidence classification as Addendum E §E.0:

| Class | How it's marked below |
|---|---|
| **(i) Repo-checked, this session** | Claude independently ran a read-only check against the repository during this documentation session. | "**Repo-verified**" |
| **(ii) Founder/prior-session reported** | Production/runtime facts supplied in conversation; Claude did not independently re-execute them. | "**Reported**" |
| **(iii) Historical record** | Already recorded with its own evidence in Addenda A–E or §§1–24. | "**Per [addendum/section]**" |

## F.1 P3.9 scope

P3.9 is the separately-scoped follow-up to the two items Addendum E explicitly left open (Addendum E §E.4/§E.5): (1) the mobile production build (§B.4 step 11) and (2) a fresh multi-company switch E2E against the production environment (§B.4 step 12). This addendum records the outcome of the P3.9 attempt to close those items.

## F.2 Verified items

| Item | Status | Basis |
|---|---|---|
| Connector prerequisite — P3.9 connector process running, bound to Vighnaharta's connector identity, targeting the production WS | **VERIFIED** | **Reported** (prior session), consistent with the prerequisite documented in the E2E script header (`validation/p39_prod_multicompany_e2e.ps1`). The recorded `.p39_e2e_result.json` stops at step `me` and never reached the `connector_status` step, so no fresh connector-status number is claimed here. |
| Genuine EAS production build (§B.4 step 11 / Addendum E open item 1) | **VERIFIED** | **Reported** via genuine EAS build artifacts on expo.dev (external to the repo). **Repo-verified** partial: `mobile/eas.json` contains a `production` profile (android `app-bundle`, `EXPO_PUBLIC_API_BASE_URL=https://books.gcwealthguru.com`). Note: the in-repo `validation/eas_build_*.log` files (2026-07-28) are **development-profile** runs and are not evidence of the production build. |
| Membership implementation — investigated | **VERIFIED CORRECT** | **Repo-verified** this session: `backend/app/services/company_service.py` `add_member` raises `InsufficientRole` unless the acting member's role is `owner`; membership is tenant-scoped via the `UserCompany` junction (`backend/app/models/company.py`); auth routes expose only `register`/`login`/`refresh`/`me`/`change_password`; `create_company` creates a **new** company with the caller as owner. There is **no self-service "join existing company" / cross-tenant membership path** and no admin/superuser/bootstrap bypass. |
| Application defect | **NONE IDENTIFIED** | Repo-verified behavior is consistent with the intended owner-granted, tenant-scoped membership model; no application defect was identified. |

## F.3 Item 12 — production multi-company E2E — **OPEN / NOT VERIFIED**

**Reason:** Production multi-company E2E cannot proceed because the required E2E account cannot be legitimately granted membership in the existing GURUDEV ENGINEERS company. The existing company's owner identity is not documented in project evidence, and the application provides no cross-tenant self-service membership path.

Repo evidence supporting this record:
- `validation/.p39_membership_diag_result.json` (2026-09-03T15:09:55+05:30): login user `dc640ebc-ae11-42c4-98ab-24e0121e78e2` / `cmagauravchandaliya@gmail.com`; `/auth/me` returns exactly one company — Vighnaharta Agro Chemicals (`32a51be2-13f5-4b75-a67e-0f1d77b3121f`), role `owner`; `companyCount=1`; **no GURUDEV ENGINEERS membership**; login user id matches the recorded `.user_id`.
- `validation/.p39_e2e_result.json` (2026-09-03T15:51:36+05:30): step `login` OK; step `me` **FAIL** — `vighnaharta=32a51be2-…/owner gurudev=/`.

Item 12 is therefore **NOT VERIFIED** and **remains OPEN**. It is not closed, not bypassed, and not re-run.

## F.4 What was NOT done (explicit)

Per the recording instruction, none of the following was performed:
- The production multi-company E2E was **not** re-run, and no E2E evidence was fabricated.
- No production system, database, or configuration was modified.
- No authentication/authorization code or data was changed; no admin/superuser/bootstrap bypass was introduced or used.
- No duplicate GURUDEV ENGINEERS company was created, and no membership was fabricated.

## F.5 Required operational action

Completion of Item 12 requires an **operational action by the existing GURUDEV ENGINEERS owner** (whose identity is not documented in project evidence): that owner must grant the E2E account (`dc640ebc-ae11-42c4-98ab-24e0121e78e2` / `cmagauravchandaliya@gmail.com`) membership in GURUDEV ENGINEERS through the application's owner-granted membership path (`POST /api/v1/companies/{company_id}/members`). Until then, the controlled multi-company switch E2E against production cannot legitimately proceed.

## F.6 Status

**P3.9 Item 12 (production multi-company switch E2E) — OPEN / NOT VERIFIED**, operationally blocked on the unknown existing-company owner identity (§F.3, §F.5). The connector prerequisite and the genuine EAS production build are verified (§F.2); the membership implementation was investigated and found correct, with no application defect identified. P3.8 production deployment status per Addendum E is unchanged; production readiness beyond the controlled pilot remains a separate, future gate per §1/§24.

---

# ADDENDUM G — P3.9 Item 12: production multi-company switch — **CLOSED (via a different, legitimately-owned company pair)**

**Recorded:** 2026-09-12. **Additive milestone entry.** Addenda A–F and §§1–24 above are unchanged and remain their own dated records; nothing above is edited, deleted, or renumbered.

## G.0 What this addendum does and does not claim

It does **not** claim the GURUDEV ENGINEERS cross-tenant blocker from §F.3/§F.5 is resolved — that company's owner identity is still undocumented, no membership was granted or fabricated, and that specific path remains untouched. It **does** claim Item 12's underlying functional question — *does a production multi-company switch actually work end-to-end?* — is now answered **yes**, evidenced against the *same* account Addendum F evaluated (`dc640ebc-ae11-42c4-98ab-24e0121e78e2` / `cmagauravchandaliya@gmail.com`), using a second company that account legitimately owns outright, created during this session rather than the blocked pre-existing one.

Evidence classes follow §E.0/§F.0 (**Repo-verified** = Claude independently ran a read-only check this session; **Operator-reported** = supplied in conversation, not independently re-executed by Claude; **Session-observed** = an artifact — a screen recording — the operator supplied and Claude directly inspected, frame by frame, this session).

## G.1 What changed to make this possible

Two defects, found and fixed live during this session, were blocking any multi-company switch attempt for this account — independent of the GURUDEV cross-tenant issue:

1. **Mobile:** `TallySetupScreen` always offered "Choose an existing company" for an unmapped discovered Tally company, even when every company the account owns is already bound to a *different* Tally company — guaranteed to fail with `tally_mapping_collision`. Fixed: commit `8238ceb` (hide the dead-end option), then superseded/extended by commit `e684ddf` (one-tap "Connect this company" using the discovery's own name/GSTIN/financial-year data, no manual form).
2. **Backend:** `GET /connector/{id}/tally-companies` computed each discovery's `mapped_to_backend_company_id` only from `ConnectorCompanyBinding` rows. The account's own pre-existing company (Vighnaharta Agro Chemicals) has `Company.tally_master_id` set via a historical path that never wrote a binding row — zero `ConnectorCompanyBinding` rows existed in production at all — so the endpoint reported it as "Unmapped" even though it was genuinely taken, which is what triggered the collision in the first place. Fixed: commit `09a865a` (fold `Company.tally_master_id` into the mapped-lookup), **deployed to production this session** (image rebuilt from `/opt/taxmind/app/backend`, `taxmind-prod-taxmind-api-1` recreated, verified healthy, clean startup log, WS clients reconnected automatically).

## G.2 Evidence

| Item | Status | Basis |
|---|---|---|
| Second company created and owned by the account | **Repo-verified** | Production `companies` row `5cfa379a-c429-445c-808f-b61c04af2ea2`, name "HGURUDEV ENGINEERS - (from 1-Apr-25)", `tally_master_id = 03e8b75c-1fe2-469b-92b9-6d9017e3d1c2` (matches the real Tally company's GUID from the connector's discovery scan). `user_companies` confirms `dc640ebc-ae11-42c4-98ab-24e0121e78e2` (`cmagauravchandaliya@gmail.com`) holds `owner` on **both** this company and the pre-existing Vighnaharta company (`32a51be2-13f5-4b75-a67e-0f1d77b3121f`). |
| Mapping performed through the real API, not fabricated | **Repo-verified** | `audit_logs`: `company.created` then `company.tally_mapping_configured` for `5cfa379a-…`, both at 2026-09-12 14:31:39 UTC, under one second apart — i.e. the one-tap flow's actual create-then-map call sequence, not a manually-inserted row. |
| The switch itself was exercised on-device, against production | **Session-observed** | A screen recording from the operator's real device (production `EXPO_PUBLIC_API_BASE_URL`) was inspected frame-by-frame this session, showing the Tally Setup screen, the (at-the-time-still-buggy) collision error, then — after the fixes above shipped — successful company creation and mapping. |
| The account can actually switch between the two companies in the app | **Operator-reported** | Direct, first-person operator statement this session: "i am able to switch between companies." |
| No unintended data change | **Repo-verified** | Vighnaharta's `tally_master_id` (`c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9`) was re-checked immediately after the (pre-fix) collision attempt and is unchanged from its value before this session; the collision attempt correctly produced no `company.tally_mapping_configured`/`_changed` audit entry, confirming the backend's own guard rejected it rather than silently overwriting anything. |

## G.3 Verdict

**Item 12's functional intent — a production multi-company switch, actually working — is now VERIFIED**, for account `dc640ebc-ae11-42c4-98ab-24e0121e78e2` across companies `32a51be2-…` (Vighnaharta) and `5cfa379a-…` (HGURUDEV ENGINEERS), both owned outright by that account. The original §F.3 blocker (self-service access to the *pre-existing*, separately-owned GURUDEV ENGINEERS company) is **unchanged and still open** if that specific company is ever required for a future test — it was not touched, and no membership was granted or fabricated against it. As a **general production-readiness signal**, however, Item 12 is no longer an open question: the switch mechanism itself is confirmed working end-to-end in production, using real data, by the account's real owner.

## G.4 Status

**P3.9 Item 12 — CLOSED as a general production-readiness item (§G.3)**, via evidence against a legitimately-owned company pair rather than the originally-targeted GURUDEV ENGINEERS company (that narrower path remains open per §F.3/§F.5, untouched by this addendum). Two real defects blocking this were found and fixed in the same session (§G.1), with the backend fix deployed to production. P3.8 production deployment status per Addendum E is unchanged; production readiness beyond the controlled pilot remains a separate, future gate per §1/§24.

---

# ADDENDUM H — §18 UTC/IST dashboard day-boundary item — **FIXED (code), NOT DEPLOYED**

**Recorded:** 2026-09-15. **Additive milestone entry.** Addenda A–G and §§1–24 above are unchanged and remain their own dated records; nothing above is edited, deleted, or renumbered.

**Scope.** Closes the code side of the §18 known limitation "Dashboard 'today' computed in UTC, not IST (P0.31 follow-up)" and the corresponding §20 backlog line ("Timezone-aware date boundaries"). **Repo-verified this session** (Claude ran the tests and read the diff directly; nothing here is operator-reported):

- Migration `0020`: additive `companies.timezone` (IANA name, `NOT NULL DEFAULT 'Asia/Kolkata'` — correct for every existing company, since TaxMind Books is India-only).
- `app/core/company_time.py::company_today(company, now=...)` — the one place "today" is computed for a company, converting through the company's timezone rather than using UTC or the server's local clock.
- Wired into every place §18 named: `dashboard_service.build_dashboard` (`today`/`this_month`), `GET /reports/trial-balance`, `/profit-loss`, `/balance-sheet`, `/outstanding` (`as_of_date`/`from_date`/`to_date` defaults), and `GET /dashboard/financials`. All previously called `datetime.now(UTC).date()` or the server-local `date.today()` — exactly the "reports endpoints use a different clock" gap §18 flagged.
- Tests: 7 new unit tests on `company_today` (the exact 00:00–05:29 IST boundary case from §18, naive-`now` handling, a non-IST company, an invalid-timezone fallback) + 2 new integration tests proving `build_dashboard` counts a voucher correctly across the boundary. Full backend suite 723/723 passing (up from 713 after Addendum-adjacent P3.2 work the same session), ruff clean, mypy clean on all touched files.

**Not done by this addendum:** migration `0020` has not been applied to the production database, and this fix has not been deployed. Per §B.4/§E's convention, code being correct in the repository is not evidence of a production state change. Until deployed, production dashboards and reports still compute "today" the old (UTC) way.

## H.1 Status

**§18/§20 timezone item — FIXED IN CODE, NOT YET DEPLOYED.** Follows the same deploy sequence as any other backend change (`CLAUDE.md` §"Production deployment"): `scp` the changed files, rebuild the `taxmind-api` image, `alembic upgrade head` to apply `0020`, recreate the container, verify `/health`. Production readiness beyond the controlled pilot remains a separate, future gate per §1/§24; this addendum closes one named §20 backlog line, not the gate itself.