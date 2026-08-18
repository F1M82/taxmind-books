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