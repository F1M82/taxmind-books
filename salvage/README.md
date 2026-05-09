# Salvage — files carried forward from Qwen-Tallyonmobile

**Status:** Reference material. Read by Coder Claude during specific Phase 0 tasks.
**Source:** `github.com/F1M82/Qwen-Tallyonmobile` (now archived).
**Scope:** Two files only. Everything else from that repository is discarded per `docs/REPO_LAYOUT.md` Section "Discard."

This directory contains the only assets reused from the abandoned predecessor codebase. They are placed here, separate from the new code under `backend/` and `connector/`, because:

1. They have not yet been adapted to the v1.2 architecture
2. They are reference material, not production code
3. Once they are adapted into the new tree, the originals here are no longer authoritative

## Files

### `tally_client.py` (467 lines)

The TallyPrime XML/HTTP client. Methods to ping Tally, fetch ledgers, fetch groups, post vouchers, fetch trial balance, fetch outstanding bills.

**Used by:** Phase 0 task **P0.21 — Connector: salvage tally_client.py**.

**What to do with it:** copy into `connector/connector/tally_client.py`, then clean up:
- Add type hints where missing
- Update import paths to match the new project layout
- Add unit tests using `pytest-httpx` against canned XML responses (per task P0.21 acceptance criteria)
- Add the v1.2 changes from task **P0.46**:
  - `post_voucher()` accepts an `as_optional` parameter; emits `<ISOPTIONAL>Yes</ISOPTIONAL>` when set
  - New method `approve_optional_voucher(voucher_guid)` — alters voucher to clear the Optional flag
  - New method `reject_optional_voucher(voucher_guid)` — deletes the voucher entirely

**What NOT to do:**
- Do not import this file directly from `backend/`. The connector and backend are independent processes; they communicate over WebSocket per `docs/CONNECTOR_PROTOCOL.md`.
- Do not preserve any orchestration code that reaches outside this single class. The original repo's `connector/main.py` had a different message protocol; that orchestration is rewritten from scratch per `CONNECTOR_PROTOCOL.md`.

**Why this file is good salvage:** the XML envelope construction matches TallyPrime's documented format, the methods cover the surface area we need, and the parsing helpers handle Tally's idiosyncratic response shapes. Recreating this from scratch would take a week; cleaning up the existing version takes a day.

---

### `old_models.py` (236 lines, originally `backend/models/__init__.py`)

The SQLAlchemy ORM models from the predecessor: Company, Ledger, Voucher, LedgerEntry, ReconciliationSession, ReconciliationMatch, AuditLog, User, UserCompany, PaymentDetection, plus three enums.

**Used by:** Phase 0 tasks **P0.05, P0.06, P0.07, P0.08** — model creation.

**What to do with it:** read it as a reference for shape and intent. Do **not** copy verbatim. The new models live in `backend/app/models/`, organized one file per aggregate per `docs/REPO_LAYOUT.md`:
- `models/user.py` — User
- `models/company.py` — Company + UserCompany association
- `models/ledger.py` — Ledger
- `models/voucher.py` — Voucher + LedgerEntry
- `models/audit_log.py` — AuditLog
- `models/ingestion.py` — Ingestion + (in v1.2, no DraftVoucher; ingestion links directly to vouchers)
- `models/reconciliation.py` — ReconciliationSession + ReconciliationMatch
- etc.

The new models must produce a schema **identical** to `docs/SCHEMA.sql`. SCHEMA.sql is the source of truth, not this old file. Where the old file disagrees with SCHEMA.sql (and it does in many places), SCHEMA.sql wins.

**Specific differences vs the old file:**
- v1.2 adds `is_optional_in_tally`, `approved_to_regular_at`, `approved_to_regular_by`, `optional_rejection_reason`, `optional_rejected_at`, `optional_rejected_by` to Voucher
- v1.2 adds Ingestion + DraftVoucher tables (well, Ingestion only — DraftVoucher was dropped in v1.2 per Flow B)
- v1.2 adds extraction_quotas, cost_tracking, account_deletion_requests, data_export_requests, device_tokens
- v1.2 has stricter constraints (CHECK constraints on GSTIN/PAN/pincode formats; DEFERRABLE on voucher number uniqueness)
- v1.2 uses TenantScopedMixin per `docs/TENANCY.md`
- v1.2 uses `MoneyColumn` (Numeric(15,2)) per `docs/MONEY.md`
- v1.2 uses string→Decimal serialization per `docs/MONEY.md`

**Why this file is good salvage:** the table shapes are 80% correct, the FK relationships are sound, and the enum values match Tally's vocabulary. Reading it before authoring `models/voucher.py` will save time on shape decisions.

**What NOT to do:**
- Do not `from old_models import *` anywhere in `backend/`. This file is reference-only.
- Do not reuse the old enum classes. The new enums are defined in SCHEMA.sql and mirrored in the model files.
- Do not preserve `PaymentDetection` — it was a half-built feature; v1.2's ingestion pipeline replaces it.

---

## How Coder Claude uses this directory

Per `docs/PHASE_0_TASKS.md`, the salvage directory is referenced explicitly in:

- **P0.05** — Models User/Company: read `old_models.py` for shape; implement per SCHEMA.sql.
- **P0.06** — Models Ledger: same.
- **P0.07** — Models Voucher: same.
- **P0.08** — Models AuditLog + idempotency_keys: same; the old AuditLog is a starting reference.
- **P0.21** — Connector: salvage tally_client.py: copy and clean up.
- **P0.46** — Connector Optional voucher commands: extend the cleaned-up tally_client.py.

For all other tasks, this directory is irrelevant. Coder Claude does not browse here for inspiration on tasks unrelated to the above.

---

## After salvage is complete

Once the cleaned-up files have landed in `connector/connector/tally_client.py` and `backend/app/models/*.py`, this `salvage/` directory may be:

- Kept (recommended) — preserves the audit trail of what was reused vs rewritten
- Moved to `archive/` — out of the working tree but in git history
- Deleted — discouraged; loses the provenance record

The decision is yours, post-Phase 0. Coder Claude does not delete this directory autonomously.

---

## Why the rest of Qwen-Tallyonmobile was not salvaged

For completeness, `docs/REPO_LAYOUT.md` Section "Discard" lists what was rejected:

- The reconciliation engine (`services/reconciliation/matching_engine.py`) — wrapped in a docstring, never executable
- Four imported but missing modules (`partial_payment_matcher.py`, `excel_parser.py`, `pdf_extractor.py`, `certificate_generator.py`)
- The duplicate `backend/ models/` directory (with leading space) containing divergent copies
- The 1,871-line test suite that could not collect any tests
- All API routes — most returned hardcoded responses; rewritten from scratch per `docs/API.md`
- The mobile screens — most were UI placeholders without onPress handlers; rewritten per `docs/REPO_LAYOUT.md`
- The connector main loop — used a different (incompatible) message protocol; rewritten per `docs/CONNECTOR_PROTOCOL.md`

These two salvage files are the only genuine production-quality work in the predecessor repository. Everything else cost more to read and adapt than to rewrite cleanly.
