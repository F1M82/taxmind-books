# Amendments — v1.3

**Status:** Frozen. Supersedes the corresponding sections of v1.2.
**Date:** 14 May 2026.

This document records all changes from v1.2 to v1.3. The 15 documents from v1.2 have been edited inline; this file is the human-readable summary of what changed and why.

If a v1.3 amendment conflicts with v1.2 text, the amendment wins.

---

## Summary of changes

One coordinated feature set, approved through the constitution Section 1 stop-and-justify flow: **multi-Tally-company support for one CMA practice managing many MSME clients on one Tally install.**

### The user need

The architect of TaxMind Books is a practicing CMA. His own use case involves managing 50+ client books inside one TallyPrime install on one PC. v1.2 binds one connector to one Tally company; cross-contamination was the only protection. v1.3 removes that limit by making the connector aware of all Tally companies on the host and routing operations to the right one.

The same change benefits any CMA firm with multiple clients in one Tally install — likely most of TaxMind Books' future customers.

### Behavior changes

**1. Connector reads Tally data folder, lists all Tally companies.**
The connector is configured with a `tally_data_folder_path` at install time (defaults inspected automatically; user can override). On first connection it enumerates all company subdirectories (each is a numbered folder under Tally's data root with a `manager.500` or equivalent metadata file). Result is reported to the backend as the list of available Tally companies for that connector.

**2. Backend company maps to exactly one Tally company.**
Each backend company stores: `tally_data_folder_path`, `tally_company_identifier` (Tally's internal company ID — a numeric directory name), and `tally_company_display_name` (the company name a human reads). Multiple backend companies on one connector are supported; each binds to one Tally company.

**3. Mobile UI shows the Tally company list and lets user map.**
A new screen "Tally Setup" lists the discovered companies. User selects which backend company maps to which Tally company. The mapping is reversible (admin-only) but logged in audit.

**4. "Refresh active company" check.**
A button in mobile UI (and a periodic heartbeat-driven check) asks the connector: "Which Tally company is currently open on this machine?" The connector queries Tally's XML API for the active company and returns the answer. The mobile UI shows the active company prominently with a green/red indicator.

**5. Voucher post: queue-on-mismatch model.**
When a voucher is posted from mobile:
- Backend creates the voucher row (status `pending_tally_post`).
- Connector receives the post command with the target Tally company identifier.
- Connector checks "is the right Tally company currently open?"
- If YES: post proceeds as in v1.2 (Optional or Regular per existing rules).
- If NO: connector returns `wrong_company_open`. Backend keeps the voucher queued. No data lands in the wrong Tally company.
- When Tally switches to the right company (detected via heartbeat), the backend's posting task re-fires and drains the queue for that company.

**6. Queued vouchers expire after 30 days.**
A daily Celery beat task scans for vouchers older than 30 days in `pending_tally_post` state. Each expiring voucher: (a) sends a notification to the company owner with the voucher detail and the reason "no Tally activity for this company in 30 days", (b) marks the voucher `tally_post_expired`, (c) writes an audit row.

**7. Ledger creation from mobile.**
Users can create ledgers from the mobile app. The ledger is created in the backend, then synced to Tally via a new connector command. **Critical rule:** any voucher that references a ledger which is newly created (not yet confirmed as present in Tally) lands in Tally as an **Optional voucher** regardless of confidence. The Optional voucher mechanism enforces human review at the Tally machine when new entities enter the system.

**8. Multiple connectors per backend company allowed.**
Two staff in one CMA firm can have Tally installed on two different PCs, both syncing with the same backend company. Audit log records `posted_by_connector_id` on every voucher to attribute who/which-machine did each post. Idempotency keys prevent double-post when both connectors try the same voucher.

**9. Connector deployment site is locked.**
v1.3 documentation makes explicit: the connector **must be installed on the PC that hosts the Tally data folder** — for Silver edition this is the daily-driver PC, for Gold edition this is the data-server PC. Foreground-only execution. Windows Service install (for headless server deployments) deferred to a later version. TallyPrime Cloud is out of scope.

### Explicitly NOT changed in v1.3

- **Group company / consolidation.** Multiple Tally companies merging into one backend company's reports remains a Phase 4+ feature.
- **TDL (Tally Definition Language).** Still not used; HTTP/XML on port 9000 remains the integration surface.
- **Tally Server / multi-user concurrency-safe writes from non-Tally clients.** v1.3 connector reads Tally data folder for company discovery only; writes still go through Tally's XML API one company at a time.
- **Offline-first mobile.** Pushed to v1.4 / Phase 2. v1.3 mobile remains online-required for browsing and posting.

### Revised phase timeline (solo)

| Phase | v1.2 | v1.3 |
|---|---|---|
| Phase 0 closeout | already running | unchanged — finish Section 7.5–7.11 against v1.2 architecture |
| **Phase 0.5 (new) — v1.3 multi-company** | — | ~2 weeks (9–10 days coding) |
| Phase 0.5 validation | — | ~1 day human |
| Phase 1 (wedge: invoice scan) | ~5.5 weeks | ~5.5 weeks (unchanged) |
| Total to wedge live | ~10–11 weeks from start | ~12–13 weeks from start |

The added ~2 weeks buys the multi-client UX. Without it, the architect of the product cannot use it on his own CMA practice — and the product cannot be sold to any CMA firm with more than one client on one Tally install (which is most of them).

---

## File-by-file diff

What changed where.

### `ARCHITECTURE.md`
- Version bumped to 1.3
- Doc map updated (this file added)
- Phasing table adjusted (Phase 0.5 inserted between Phase 0 and Phase 1)
- Changelog entry added

### `SCHEMA.sql`
- **Modified `companies` table** — added columns:
  - `tally_data_folder_path VARCHAR(500) NULL`
  - `tally_company_identifier VARCHAR(100) NULL` (Tally's internal numeric ID for the company)
  - `tally_company_display_name VARCHAR(255) NULL`
  - `tally_mapping_configured_at TIMESTAMPTZ NULL`
  - `tally_mapping_configured_by UUID NULL REFERENCES users(id)`
- **Modified `vouchers` table** — added:
  - `posted_by_connector_id UUID NULL` (no FK — connectors come and go, this is informational; full connector UUID lives in audit too)
  - `tally_post_expired_at TIMESTAMPTZ NULL`
  - New `voucher_status` enum value: `'pending_tally_post'`, `'tally_post_expired'`
- **Modified `ledgers` table** — added:
  - `created_via_mobile BOOLEAN NOT NULL DEFAULT FALSE` (mobile-created ledgers taint vouchers that reference them as Optional until ledger is confirmed in Tally)
  - `confirmed_in_tally_at TIMESTAMPTZ NULL`
- **Modified `connector_enrollments` table** — added:
  - `tally_data_folder_path VARCHAR(500) NULL`
  - `last_company_list_sync_at TIMESTAMPTZ NULL`
- **New table `tally_companies_discovered`** — caches the list of Tally companies a connector has observed, refreshed on demand
- **Indexes** — partial indexes added for `pending_tally_post` retry and `created_via_mobile` ledger Optional-tainting

### `CONNECTOR_PROTOCOL.md`
- **New connector→backend message** `tally_companies_list_result` — payload is the discovered company list
- **New backend→connector commands:**
  - `list_tally_companies` — read the data folder, return all companies
  - `get_active_tally_company` — query Tally XML API for currently-open company
- **Modified `post_voucher` command** — now includes `target_tally_company_identifier`. Connector verifies match before posting. On mismatch, returns `wrong_company_open` (retryable; backend queues).
- **Modified `register` message** — now includes `tally_data_folder_path` so backend knows the connector's data root
- **New event message** `tally_company_changed` — connector emits when it detects the active Tally company has switched. Backend uses this signal to trigger queue drain for the newly-active company.

### `API.md`
- **New endpoint** `GET /api/v1/connector/{connector_id}/tally-companies` — fetch the discovered company list (admin-only)
- **New endpoint** `POST /api/v1/companies/{id}/tally-mapping` — set/update the Tally folder + company ID + display name for this backend company
- **New endpoint** `GET /api/v1/companies/{id}/tally-mapping` — read current mapping
- **New endpoint** `DELETE /api/v1/companies/{id}/tally-mapping` — clear mapping (audit-logged)
- **New endpoint** `GET /api/v1/vouchers/?status=pending_tally_post` — list queued vouchers (already partially supported by status filter; documented explicitly)
- **New endpoint** `GET /api/v1/connector/{id}/active-tally-company` — refresh and return active Tally company
- **Modified** `POST /api/v1/ledgers/` — when called from mobile, automatically sets `created_via_mobile=true`; ledger lands in DB but is queued for Tally sync
- **Modified** voucher creation responses — if the target Tally company is not currently active, response includes `tally_post_status: "queued"` and `queue_reason: "wrong_company_open"` rather than failing
- **Error codes added:**
  - `tally_mapping_required` (412) — voucher post requires the company to have a Tally mapping
  - `wrong_company_open` (202) — voucher accepted and queued because Tally is on a different company
  - `tally_post_expired` (410) — voucher queued >30 days, now expired

### `AUDIT.md`
- **New actions:**
  - `company.tally_mapping_configured`
  - `company.tally_mapping_changed`
  - `company.tally_mapping_cleared`
  - `voucher.tally_post_queued` (replaces or supplements `voucher.tally_post_failed` for the queue-on-mismatch case)
  - `voucher.tally_post_expired`
  - `ledger.confirmed_in_tally`
  - `connector.tally_companies_discovered`
- **AuditLog.metadata** now records `posted_by_connector_id` for `voucher.posted_to_tally`, `voucher.posted_as_optional`, `voucher.tally_post_failed` events. The connector ID is captured from the WebSocket connection that issued the post.

### `EXTRACTION_CONTRACT.md`
- Section "Auto-post threshold" updated:
  - Existing rule: AI-extracted vouchers always land as Optional in Tally.
  - **New rule (v1.3):** if a voucher references any ledger where `created_via_mobile=true AND confirmed_in_tally_at IS NULL`, the voucher is forced to Optional regardless of any other criterion. This applies to manually-entered vouchers too, not just AI-extracted.

### `PHASE_0_TASKS.md`
- New section "Phase 0.5 — v1.3 multi-Tally-company support"
- **New tasks P0.49 through P0.57:**
  - P0.49 — Schema migration for v1.3 columns and new table
  - P0.50 — Connector: read Tally data folder, list companies
  - P0.51 — Connector: new `list_tally_companies`, `get_active_tally_company` commands, `tally_company_changed` event
  - P0.52 — Backend: Tally mapping CRUD endpoints
  - P0.53 — Backend: voucher dispatcher with queue-on-mismatch
  - P0.54 — Backend: queued voucher expiry task (30-day Celery beat)
  - P0.55 — Mobile: Tally Setup screen (company list, mapping UI, active-company indicator)
  - P0.56 — Mobile: voucher entry honors the active mapping; ledger creation works
  - P0.57 — Validation: re-run Sections 7.5–7.11 with v1.3 connector flow

### `CONNECTOR_ENROLLMENT.md`
- Added "Silver vs Gold deployment" section
- Added "Where to install the connector" guidance (must be on Tally data-host PC)
- Added "Configuring Tally data folder path" step
- Path A (manual admin) runbook updated with the new fields

### Documents NOT changed

`TENANCY.md`, `MONEY.md`, `IDEMPOTENCY.md`, `TESTING.md`, `REPO_LAYOUT.md`, `VALIDATION_REPORT.md`, `REPORTS.md` — unchanged in v1.3.

---

## Validation impact

`VALIDATION_REPORT.md` Section 7 gets new subsections (Section 7 already covers 7.1–7.11 from v1.2; v1.3 adds):

- **Section 7.12 — Tally company discovery.** Connector reads data folder, lists all companies present, backend receives and stores list.
- **Section 7.13 — Mapping setup.** Mobile lists discovered companies, user maps backend↔Tally, mapping persists.
- **Section 7.14 — Active company detection.** Connector reports currently-open Tally company on demand. Mobile reflects.
- **Section 7.15 — Voucher queueing on mismatch.** Post voucher while Tally has wrong company open → voucher is created, queued, audit logged with reason. Switch Tally to right company → voucher posts within heartbeat window.
- **Section 7.16 — Queue expiry.** Voucher older than 30 days in queue → notification fires, voucher marked expired, audit row created.
- **Section 7.17 — Mobile ledger creation tainting.** Create a ledger via mobile. Create a voucher using it. Verify the voucher lands as Optional in Tally regardless of confidence.
- **Section 7.18 — Multiple connectors.** Two connectors enrolled to same backend company. Post a voucher. Verify exactly one connector posts it (via idempotency). Audit log records which one.

---

## Patches recorded under v1.3

v1.3 amendments include the audit-FK note already captured during Phase 0:

- **AuditLog `user_id ON DELETE SET NULL` is dead** while the append-only trigger is in force. v1.3 documents this explicitly in AUDIT.md as a "Foreign key behavior under deletion" subsection. Account-deletion flow (P0.45) handles user deletion via PII scrub, not row delete. No code change needed; doc note only.

---

## What remains unchanged

- Constitution stays in force.
- All other cross-cutting docs from v1.2 stand.
- Tenant isolation, audit logging, money handling, idempotency — unchanged rules.
- Optional voucher mechanism stays the safety net for AI-extracted entries and now (v1.3) also mobile-created ledger-tainted vouchers.
- TaxMind Books does NOT use TDL, does NOT support TallyPrime Cloud, does NOT consolidate group companies — these stay deferred or out of scope.

What's new in v1.3 is the ability for one CMA practice to serve many MSME clients from one Tally install — a real-world prerequisite for the product to be useful to its own architect, and to any future customer with the same shape.
