# Validation follow-ups

Items deferred from live validation. Each entry: what's deferred, why
it's safe to defer, and when to address it.

## P0.46d §7.5a — P&L path end-to-end (CLOSED 2026-05-18)

**Status:** resolved. Sales-voucher P&L inclusion exercised live this
session; see `validation/phase_0_20260518_074547.md` §3.2 and the
2026-05-18 entry under §7.5 Notes in `VALIDATION_REPORT.md`.

**What was deferred.** Live §7.5a re-validation (2026-05-17) proved
that a voucher entered while Tally is offline lands in
`pending_tally_post` and is included in **trial balance**. The
**profit-and-loss** inclusion path uses the same SQL filter
(`Voucher.status.in_([posted, pending_tally_post])` — see
`backend/app/services/reporting/profit_loss.py`) and has a unit-level
regression test (`test_profit_loss_includes_pending_tally_post_vouchers`),
but it had not been exercised end-to-end against live TallyPrime data.

**How it was closed (2026-05-18).** Sales ledger `eb82b7bd-…` (group
`Sales Accounts`) created via `POST /api/v1/ledgers/` against the test
company. POST `/api/v1/vouchers/` with Tally stopped → voucher
`2d555535-…` (Sales, Xyz Ltd Dr 100 / Sales Cr 100, status
`pending_tally_post`). `GET /api/v1/reports/profit-loss?
from_date=2026-04-01&to_date=2026-05-18` returned
`income.ledgers=[{Sales: 100.00}]`, `income.total=100.00`,
`net.value=100.00 profit` while `tally_posted_at` remained null on the
voucher. Closes this entry.
