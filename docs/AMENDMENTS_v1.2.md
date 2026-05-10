# Amendments — v1.2

**Status:** Frozen. Supersedes the corresponding sections of v1.1.
**Date:** 8 May 2026.

This document records all changes from v1.1 to v1.2. The original 13 documents have been edited inline; this file is the human-readable summary of what changed and why.

If a v1.2 amendment conflicts with v1.1 text, the amendment wins.

---

## Summary of changes

Two product changes and one bundle of additions, approved through the constitution Section 1 stop-and-justify flow.

### Change A — Auto-post via Tally Optional vouchers (Flow B)

**v1.1 behavior:** AI-extracted invoices became `DraftVoucher` rows. If confidence ≥ 0.90 AND amount ≤ ₹1,00,000, the draft auto-posted to Tally as a Regular voucher. Otherwise it sat in a review queue.

**v1.2 behavior:**
- The amount cap is **removed**. Confidence is the only auto-post criterion.
- Every AI-extracted entry posts to Tally as an **Optional voucher**, regardless of confidence.
- Optional vouchers exist in Tally but have **zero financial impact** — they don't appear in P&L, balance sheet, or trial balance until approved.
- Admin reviews via mobile app or directly in Tally and either:
  - **Approves** → flips Optional to Regular in Tally → voucher now affects financial statements
  - **Rejects** → deletes the Optional voucher from Tally entirely
- The `DraftVoucher` table is **removed**. Vouchers go directly to the `vouchers` table with `is_optional_in_tally = true`.

**Manual entries (typed by user on mobile):** post as **Regular**, immediately. No Optional staging. Self-entered = self-approved.

**Rule that generalizes across phases:**
- AI-extracted (invoice scan, SMS, email) → Optional, needs approval
- Human-entered (manual mobile/web) → Regular, no approval needed

### Change B — Reports moved to Phase 0; Analytics in Phase 1

**v1.1:** Reports and analytics deferred to Phase 5.

**v1.2:** Phase 0 ships trial balance, P&L summary, balance sheet, outstanding receivables/payables. Phase 1 adds GST liability summary, aged outstanding, top-5 lists, cash flow chart.

### Change C — Industry-standard MSME features added

Per your authorization to add features I judge industry-appropriate. Added:

- **A1** Push notifications (Phase 0/1)
- **A3** Daily/weekly home dashboard on mobile (Phase 0)
- **A4** Backup export (Phase 1)
- **A5** GST liability summary on dashboard (Phase 1)
- **B1** Aged outstanding 0/30/60/90+ buckets (Phase 1)
- **B2** TDS payable summary (Phase 1)
- **B3** Top 5 debtors / creditors / expense heads (Phase 1)
- **B4** Cash flow chart, 6-month (Phase 1)
- **C1** Soft account deletion + 30-day grace (Phase 0)
- **C2** Data export request flow — DPDP Act readiness (Phase 1)
- **C3** In-app onboarding checklist (Phase 0)
- **D2** Connector auto-update mechanism — spec now, build in Phase 5
- **D3** Rate-limit guards on AI extraction — daily quota per company (Phase 1)
- **D4** Cost tracking per company — token + storage + compute (Phase 1)

**Explicitly dropped from consideration:**
- A2 "Send to my CA" snapshot share — declined per your instruction.

### Revised phase timeline (solo)

| Phase | v1.1 | v1.2 |
|---|---|---|
| Phase 0 | 3–4 weeks | 5–6 weeks |
| Phase 1 | 4 weeks | ~5.5 weeks |
| Total to wedge live | ~7 weeks | ~10–11 weeks |

The added 3 weeks buy: trust features (push notifications, backup export, DPDP compliance), engagement features (dashboard, onboarding checklist), commercial features (cost tracking, rate limiting, GST liability). Worth the cost.

---

## File-by-file diff

What changed where. Use this to navigate the edits quickly.

### `ARCHITECTURE.md`
- "Phasing" section: revised effort estimates per Change B+C.
- "Critical-path data flow" section: revised step 4 (Review) and step 5 (Post) per Change A.

### `SCHEMA.sql`
- **Removed:** `draft_vouchers` table.
- **Removed:** `draft_review_status` enum.
- **Modified:** `vouchers` table — added columns `is_optional_in_tally`, `approved_to_regular_at`, `approved_to_regular_by`, `optional_rejection_reason`. Added `voucher_status` enum value `optional` and `rejected_optional`.
- **Modified:** `ingestions` table — added column `posted_voucher_id` (replaces the link via DraftVoucher).
- **Added:** `extraction_quotas` table — daily AI extraction quota per company.
- **Added:** `cost_tracking` table — per-company monthly cost rollup.
- **Added:** `account_deletion_requests` table — DPDP soft-delete tracking.
- **Added:** `data_export_requests` table — DPDP export tracking.
- **Added:** `device_tokens` table — push notification tokens (FCM / APNs).

### `EXTRACTION_CONTRACT.md`
- Auto-post threshold section: removed amount cap; auto-posting is now confidence-only AND always lands as Optional.
- Removed all references to DraftVoucher; replaced with "Voucher with is_optional_in_tally=true."

### `CONNECTOR_PROTOCOL.md`
- `post_voucher` command: added `as_optional` arg.
- New command: `approve_optional_voucher` (Optional → Regular in Tally).
- New command: `reject_optional_voucher` (delete from Tally).

### `AUDIT.md`
- Action vocabulary additions:
  - `voucher.posted_as_optional`
  - `voucher.approved_to_regular`
  - `voucher.rejected_optional`
  - `account.deletion_requested`
  - `account.deletion_completed`
  - `data_export.requested`
  - `data_export.completed`

### `API.md`
- Removed: all `/api/v1/draft-vouchers/*` endpoints.
- Modified: `POST /api/v1/ingestions/` — response now references the eventual voucher_id, not draft_id.
- Modified: `POST /api/v1/vouchers/` — manual entries land as Regular (`is_optional_in_tally=false`).
- Added: `GET /api/v1/vouchers/?is_optional=true` — list pending-approval Optional vouchers.
- Added: `POST /api/v1/vouchers/{id}/approve-to-regular`
- Added: `POST /api/v1/vouchers/{id}/reject-optional`
- Added: Reports endpoints (`/reports/trial-balance`, `/profit-loss`, `/balance-sheet`, `/outstanding`).
- Added: Analytics endpoints (`/analytics/gst-liability`, `/analytics/aged-outstanding`, `/analytics/top-debtors`, `/analytics/top-creditors`, `/analytics/top-expenses`, `/analytics/cash-flow`).
- Added: Dashboard endpoint (`/dashboard/home`).
- Added: Account / data lifecycle endpoints (`/account/deletion-request`, `/account/data-export`).
- Added: Push notification registration (`/devices/register`, `/devices/unregister`).
- Added: Onboarding endpoint (`/onboarding/checklist`).
- Added: Cost tracking (admin) endpoint (`/admin/cost-tracking`).

### `PHASE_0_TASKS.md`
- Renumbered. Added P0.36–P0.46 covering reports, dashboard, push notifications, onboarding checklist, account deletion, manual entry expansion, and the Optional/Regular voucher flow.
- Total tasks: 35 → 46.

### `REPORTS.md` (NEW)
- Defines computation rules for all reports and analytics: how to compute opening balances, treatment of Optional vouchers in totals, date-range semantics, group aggregation, currency formatting, caching strategy.

### `TENANCY.md`, `MONEY.md`, `IDEMPOTENCY.md`, `TESTING.md`, `REPO_LAYOUT.md`, `VALIDATION_REPORT.md`
- No changes. The cross-cutting concerns are unchanged in v1.2.

---

## Validation impact

The `VALIDATION_REPORT.md` template is updated in v1.2 to include:

- **Section 7.7 — Optional voucher flow** — auto-post lands as Optional, approve flips to Regular, reject deletes from Tally, financial reports exclude Optional vouchers.
- **Section 7.8 — Reports** — trial balance ties out, P&L balances, balance sheet balances, outstanding matches Tally.
- **Section 7.9 — Analytics** — GST liability accurate, aged buckets sum correctly, top-5 lists ordered correctly, cash flow chart accurate.
- **Section 7.10 — Push notifications** — extraction-complete delivered, connector-offline delivered.
- **Section 7.11 — DPDP compliance** — account deletion request → 30-day grace → hard delete; data export → email link → ZIP downloadable.

Per the Phase 0 deliverable: validation cannot pass unless reports tie out exactly to TallyPrime's own reports. This is the testable, falsifiable criterion that prevents the "looks right but isn't" failure mode.

---

## What remains unchanged

The discipline is unchanged:
- Constitution still applies. Section 1, Section 5, Section 7, Section 8 unchanged.
- All 13 cross-cutting docs from v1.1 still authoritative for what they cover.
- Tenant isolation, audit logging, money handling, idempotency — all the same rules.
- The Tally Connector salvage (`tally_client.py`) remains the carry-forward from Qwen-Tallyonmobile.

What's new in v1.2 is feature scope and the Optional-voucher safety mechanism. Nothing in the foundations changes.

---

## Patches

Patches are post-amendment corrections to internal inconsistencies that
surfaced during execution. They follow the same Section-1 stop-and-justify
flow but are recorded inline here rather than as a new versioned document.

### Patch 1 — `audit_logs.company_id` made nullable (10 May 2026)

**Surfaced during:** P0.14 implementation (auth register endpoint).

**Inconsistency.** `SCHEMA.sql` declared `audit_logs.company_id NOT NULL`,
but `AUDIT.md` requires user-lifecycle events (`user.created`,
`user.password_changed`, `user.deactivated`) to be audited — and at the
moment those events fire, the user has no tenant scope. Self-registration
in particular has no logged-in actor and no active company; writing the
required `user.created` row was impossible.

**Decision.** `audit_logs.company_id` becomes nullable. System events
(those listed in AUDIT.md §"Tenant-scoped vs system events") are written
with `company_id = NULL`. Tenant-scoped events are unchanged.

**Migration.** `backend/alembic/versions/0005_audit_logs_company_id_nullable.py`:
- `ALTER TABLE audit_logs ALTER COLUMN company_id DROP NOT NULL`
- Replace `idx_audit_logs_company_created` with the same index plus a
  `WHERE company_id IS NOT NULL` partial filter, so tenant-scoped reads
  don't pay for system-event rows.

**Model.** `AuditLog` no longer inherits `TenantScopedMixin` (which
enforces NOT NULL) and declares `company_id` as a nullable FK
explicitly. Tenant-scoped queries from the audit-log read API filter
explicitly by `company_id`; system rows are reachable only via
admin/superuser paths (Phase 5+).

**Why this isn't a v1.2 amendment proper.** It changes nothing about
product behavior, API contract, or user-visible flows. It corrects an
internal inconsistency between two architecture documents. Tenant
isolation guarantees are unchanged.
