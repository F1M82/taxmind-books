# TaxMind Books — Architecture Document

**Version:** 1.2
**Status:** Frozen for Phase 0 and Phase 1 execution
**Date:** 8 May 2026
**Author:** CMA Gaurav Chandaliya, with technical authorship by Architect Claude (Anthropic)

This document is the umbrella architecture for TaxMind Books. v1.2 supersedes v1.1 with the changes specified in `AMENDMENTS_v1.2.md`. The original 13 documents have been edited inline to reflect the v1.2 changes.

If anything in this document conflicts with one of the companion files, the companion file wins. This file's job is to provide context and navigation; the companion files are the contracts.

---

## Document map

The complete architecture is spread across 15 documents (13 original + 2 added in v1.2):

| Document | What it defines |
|---|---|
| **ARCHITECTURE.md** (this file) | Vision, scope, phasing, navigation |
| **AMENDMENTS_v1.2.md** | (NEW v1.2) Delta from v1.1 to v1.2 |
| **REPO_LAYOUT.md** | Frozen directory tree, module boundaries, naming conventions |
| **MONEY.md** | Decimal-only money handling end-to-end with three enforcement layers |
| **TENANCY.md** | Multi-tenant scoping with three FastAPI dependencies and an auto-scoping session |
| **AUDIT.md** | Service-layer audit emitter, append-only enforcement, action vocabulary (incl. v1.2 actions) |
| **IDEMPOTENCY.md** | Idempotency-Key contract, Postgres-backed, 24-hour dedup window |
| **SCHEMA.sql** | Full PostgreSQL DDL (incl. v1.2 tables: extraction_quotas, cost_tracking, account_deletion_requests, data_export_requests, device_tokens) |
| **API.md** | Endpoint contracts (incl. v1.2 endpoints: reports, analytics, dashboard, account, devices, onboarding, optional voucher approve/reject) |
| **CONNECTOR_PROTOCOL.md** | Tally Connector wire protocol (incl. v1.2 commands: approve_optional_voucher, reject_optional_voucher) |
| **EXTRACTION_CONTRACT.md** | LLM prompt, output schema, validation rules; v1.2 Flow B auto-post via Optional vouchers |
| **REPORTS.md** | (NEW v1.2) Computation rules for reports and analytics |
| **TESTING.md** | Test architecture, fixtures, CI gates, coverage thresholds |
| **PHASE_0_TASKS.md** | Atomic tasks for Phase 0 execution (35 original + 11 v1.2 tasks = 46 total) |
| **VALIDATION_REPORT.md** | Human validation template and the auto-collection script |

Coder Claude reads the relevant subset for each task. Architect Claude is the maintainer of the whole set.

---

## Product summary

**TaxMind Books** is an MSME-focused accounting automation platform. It captures accounting entries from the channels MSMEs already use — WhatsApp, SMS, photos of bills, bank statements — and posts them to TallyPrime automatically, with a CA-grade audit trail and a reconciliation review queue.

**Buyer:** the MSME owner. **User:** the MSME owner and (in some cases) their staff or part-time accountant.

**Distinct from TaxMind:** TaxMind is an AI tax intelligence platform for Chartered Accountants. TaxMind Books targets the CA's *client*. They share branding and may share an account/auth layer in future, but they are separate products with separate buyers, deployment surfaces, and price points.

**Salvage from prior work:** The Tally XML client (`connector/tally_client.py`, ~467 lines) and the database model (~236 lines, expanded here) from the abandoned Qwen-Tallyonmobile repository are the only assets carried forward. Everything else is built fresh per this specification.

---

## Operating principles

These six principles underlie every decision in this architecture:

1. **Correctness over cleverness.** Money math is not a place for cleverness. Tenant isolation is not a place for cleverness. Auditability is not a place for cleverness. Where the safe choice is verbose, take the verbose choice.

2. **Composition over rewrites.** The codebase is built incrementally. New features extend existing modules; they do not replace them. Architecture changes go through the constitution Section 1 stop-and-justify flow.

3. **Determinism over abstraction.** A function with one job, fully typed, is preferred over a flexible framework. Coder Claude finds it easier to reason about; reviewers find it easier to validate.

4. **Auditability over convenience.** Every state-changing action writes to the audit log. There are no quick wins that bypass it. The audit trail is the product's defensive moat.

5. **Stable architecture over continuous redesign.** The directory tree is frozen. The module boundaries are frozen. The contracts are frozen. Drift is not allowed without an explicit decision.

6. **Solo founder reality.** This will be built and operated by one person (Gaurav) for the first 12 months, with Coder Claude as the primary implementer. The architecture is sized for that — not for a team of ten, not for ten million users.

---

## System overview

### Components

The system is six logical components, each with a clear responsibility:

```
                              ┌────────────────┐
                              │   Mobile App   │   (primary UI)
                              │ React Native   │
                              └───────┬────────┘
                                      │ HTTPS
                                      ▼
       ┌─────────────────────────────────────────────────────┐
       │                Backend API (FastAPI)                │
       │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
       │  │   Auth   │ │ Vouchers │ │  Audit   │ │ Tenancy│  │
       │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
       │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
       │  │Ingestion │ │Extraction│ │  Recon   │ │ Tally  │  │
       │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
       └────┬──────────────┬──────────────┬───────────────┬──┘
            │              │              │               │
            ▼              ▼              ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────────────┐
    │ Postgres │    │  Redis   │    │    S3    │    │  Celery Workers│
    │          │    │ (queue + │    │ (uploads)│    │ (extraction,   │
    │          │    │ rate     │    │          │    │  posting,      │
    │          │    │ limits)  │    │          │    │  recon)        │
    └──────────┘    └──────────┘    └──────────┘    └────────┬───────┘
                                                              │
                                                       WebSocket (wss)
                                                              │
                                                              ▼
                                                  ┌──────────────────────┐
                                                  │  Tally Connector     │   (MSME's PC)
                                                  │  (Python exe)        │
                                                  └──────────┬───────────┘
                                                             │ HTTP/XML localhost:9000
                                                             ▼
                                                  ┌──────────────────────┐
                                                  │     TallyPrime       │
                                                  └──────────────────────┘
```

The tech choices and structural rules for each are in `REPO_LAYOUT.md`.

### Critical-path data flow

The spine of the product is the **capture → extract → match → review → post → audit** pipeline. Every feature plugs into it:

1. **Capture.** Inbound data arrives via mobile app (photo, voice, text), WhatsApp gateway, SMS forwarding, email IMAP, bank CSV upload. Each capture creates an `Ingestion` row.
2. **Extract.** A Celery worker pulls the ingestion, runs OCR / regex / LLM extraction depending on type, produces a structured `DraftVoucher` with a confidence score.
3. **Match.** The draft's party is matched against the company's ledger masters using GSTIN-first → PAN → fuzzy name lookup. The draft is enriched with a suggested ledger.
4. **Review.** If confidence ≥ 0.90 AND amount ≤ ₹1,00,000 AND no critical flags AND vendor matched, the draft auto-posts. Otherwise it sits in the user's review queue.
5. **Post.** An approved draft becomes a `Voucher`. The posting Celery task sends it through the Tally Connector to TallyPrime.
6. **Audit.** Every state transition writes a row to `audit_logs`. The chain is end-to-end queryable.

Each step has its own contract document. Capture and Extract are detailed in `EXTRACTION_CONTRACT.md`. Posting through the Connector is detailed in `CONNECTOR_PROTOCOL.md`. Audit is in `AUDIT.md`. Review threshold and queueing rules are in the API contracts in `API.md`.

---

## Phasing

The architecture supports a phased rollout. Each phase ships a customer-visible deliverable and is gated by the validation flow in `VALIDATION_REPORT.md`.

| Phase | Deliverable | Effort (solo, v1.2) | Tasks |
|---|---|---|---|
| **0** | Auth, multi-tenancy, audit, manual voucher creation, Tally connector, basic reports (TB/P&L/BS/Outstanding), dashboard, push notification infra, onboarding checklist, DPDP deletion | 5–6 weeks | `PHASE_0_TASKS.md` (46 tasks) |
| **1** | Invoice scan via Optional vouchers, analytics (GST liability, aged outstanding, top-N, cash flow, TDS), data export, push triggers, cost tracking, rate limiting | ~5.5 weeks | (to be authored after Phase 0 validation) |
| **2** | Bank statement ingestion (CSV + 6 PDF parsers), SMS classification with template library, Email IMAP | 6 weeks | |
| **3** | Debtor/Creditor reconciliation, GSTR-2B matching | 4 weeks | |
| **4** | (deferred) WhatsApp gateway | 6 weeks | |
| **5** | CA console, Razorpay billing, advanced reports, connector auto-update | 4 weeks | |

Total realistic calendar estimate, solo: 10–11 months.

The phase structure is conservative on purpose. The wedge — invoice scan — is Phase 1 not Phase 0 because Phase 0 must establish the foundations the wedge depends on (auth, tenancy, audit, connector). Building the wedge against a shaky foundation is what produced the prior repo failures.

---

## Out of scope (explicitly)

These are NOT features of TaxMind Books and will not be built without an explicit architectural change:

- **GSP integration** for direct GSTR fetching. Compliance overhead too high for v1; manual JSON upload is sufficient.
- **Zoho / QuickBooks / Busy support.** TallyPrime is 80%+ of the SMB accounting market in India. Other software is deferred indefinitely.
- **GSTR-1, GSTR-3B filing automation.** Filing is a regulated activity with separate compliance requirements; out of scope.
- **Inventory / stock management.** TallyPrime already does this; we do not duplicate.
- **Multi-currency / FX revaluation.** <1% of MSMEs in the target. INR-only.
- **Tally Server (multi-user Tally) special integration.** Single-user TallyPrime only in v1.
- **Mac / Linux Tally Connector.** Tally is Windows-only; the Connector is Windows-only.

If a customer asks for these, the answer is "not now." Adding them later is fine; designing around them now is scope creep.

---

## Cross-cutting concerns

### Money

Every monetary value is a Python `Decimal` end-to-end. Stored as `NUMERIC(15, 2)` in Postgres. Serialized as JSON string. TypeScript on the client uses `string`, never `number`. Three enforcement layers (types, lint, tests) make `float` a CI failure. Full spec in `MONEY.md`.

### Tenancy

Every tenant-scoped query is filtered by `company_id`, automatically, via a SQLAlchemy session that injects `WHERE company_id = ?` for any model inheriting `TenantScopedMixin`. The `X-Company-ID` header is the single source of truth for tenant scope; query parameters and body fields named `company_id` are ignored. Full spec in `TENANCY.md`.

### Audit

Every state-changing service method emits exactly one audit log row in the same DB transaction. Audit logs are append-only — Postgres role privileges plus a trigger plus a code lint enforce this. The action vocabulary is fixed; new actions are added to the spec before they are added to code. Full spec in `AUDIT.md`.

### Idempotency

State-changing endpoints accept an `Idempotency-Key` header. First request creates; replay returns the stored response. Mismatched body returns 409. The dedup table is in Postgres, not Redis, because atomicity with the action transaction matters more than throughput at this scale. Full spec in `IDEMPOTENCY.md`.

### Testing

Five-layer test pyramid: unit, integration, tenant isolation, golden corpus, E2E. Coverage threshold 80% overall, 90% for `app/core/`. CI runs lint + types + tests + migration round-trip + OpenAPI contract check on every PR. Full spec in `TESTING.md`.

### Security

JWT bearer auth with bcrypt-hashed passwords. HTTPS-only. CORS pinned to known origins, not wildcarded. Rate limiting at known abuse points (login, register, generally per-user). PII handling per DPDP Act 2023 — encryption at rest, deletion on request, breach-notification readiness. Customer financial data is never sent to LLMs except for the specific extraction calls that need it (invoice image content), and the calls don't include identifying context.

---

## Recommendations carried forward from v1.0

The strategic recommendations from v1.0 stand. Most relevant:

1. **Build the wedge first.** Phase 1 ships only invoice scan. Other features wait.
2. **Audit any contractor handover.** Use the `VALIDATION_REPORT.md` template against any future delivery.
3. **Decide Books-vs-TaxMind explicitly.** The strategic choice between running both products in parallel, folding Books into TaxMind, or parking Books for 12 months is yours. This document specifies the architecture for the standalone case; if you choose otherwise, the architecture parts are still useful as the basis for whatever you do build.
4. **Treat the audit trail as a sales asset.** CAs evaluate platforms partly on auditability. The investment here pays back.
5. **Versioned content, not versioned code, for templates.** Bank SMS formats, GSTR-2B schema variants, Tally XML quirks live in DB tables.

---

## Document conventions

- **Money:** always Decimal with 2 decimal places, in INR. Serialized as string.
- **Dates:** ISO 8601 (`YYYY-MM-DD`) at API and DB layer; localized at UI.
- **IDs:** UUIDv4 strings; never sequential integers.
- **Time zones:** store UTC; display IST.
- **Currency:** ₹ in user-facing copy; "INR" in API responses.

---

## Glossary

- **MSME** — Micro, Small and Medium Enterprise (per MSMED Act 2006)
- **BSP** — Business Service Provider (Meta-approved WhatsApp gateway)
- **GSP** — GST Suvidha Provider (GSTN-licensed portal automation intermediary)
- **GSTIN** — 15-character GST Identification Number; state-code-prefixed
- **GSTR-2A / 2B** — Auto-populated supplier invoice statements on the GSTN portal
- **TDS** — Tax Deducted at Source (Income Tax Act withholding)
- **UTR** — Unique Transaction Reference (12-digit interbank transfer ID)
- **UPI** — Unified Payments Interface (NPCI's instant payment system)
- **DPDP** — Digital Personal Data Protection Act 2023
- **Voucher** — Tally's term for an accounting entry document (Receipt, Payment, Sales, Purchase, Journal, Contra, Debit Note, Credit Note)
- **Ledger** — Tally's term for an account in the chart of accounts
- **DraftVoucher** — TaxMind Books' term for an extracted, not-yet-approved voucher candidate
- **Connector** — The Tally Desktop Connector; Windows agent bridging cloud to local Tally

---

## Changelog

**v1.2 — 8 May 2026.** Auto-post via Tally Optional vouchers (no amount cap; admin approves to Regular). Reports moved from Phase 5 to Phase 0. Analytics added in Phase 1. Industry-standard MSME features added (push notifications, dashboard, onboarding checklist, DPDP deletion/export, cost tracking, rate limiting). DraftVoucher table removed; Flow B unified queue. See `AMENDMENTS_v1.2.md`.

**v1.1 — 8 May 2026.** Added 12 companion documents that together close the gap analysis from v1.0. SCHEMA.sql provides full DDL. API.md provides endpoint contracts. CONNECTOR_PROTOCOL.md provides the wire protocol. EXTRACTION_CONTRACT.md provides the OCR contract. AUDIT.md, TENANCY.md, IDEMPOTENCY.md, MONEY.md provide cross-cutting mechanisms. TESTING.md, PHASE_0_TASKS.md, VALIDATION_REPORT.md, REPO_LAYOUT.md provide process. v1.0 prose substantially preserved in this umbrella.

**v1.0 — earlier in May 2026.** Initial strategy document. Single 13-page Word file. Superseded by v1.1.
