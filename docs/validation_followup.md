# Validation follow-ups

Items deferred from live validation. Each entry: what's deferred, why
it's safe to defer, and when to address it.

## P0.46d §7.5a — P&L path end-to-end (not yet exercised)

**What's deferred.** Live §7.5a re-validation (2026-05-17) proves
that a voucher entered while Tally is offline lands in
`pending_tally_post` and is included in **trial balance**. The
**profit-and-loss** inclusion path uses the same SQL filter
(`Voucher.status.in_([posted, pending_tally_post])` — see
`backend/app/services/reporting/profit_loss.py`) and has a unit-level
regression test (`test_profit_loss_includes_pending_tally_post_vouchers`),
but it has not been exercised end-to-end against live TallyPrime data.

**Why it's safe to defer.** The live test company has no income or
expense ledger — only HDFC BANK, Cash, Profit & Loss A/c, ABC LTD,
Xyz Ltd. A Receipt voucher (the §7.5a script's choice) only moves
balance-sheet ledgers, so it can't drive P&L. The filter is shared
with trial balance and tested at the unit level, so the risk of the
P&L path being silently broken is low.

**When to address.**

1. Create a Sales ledger via `POST /api/v1/ledgers/` (group:
   `Sales Accounts`) so the test company has an income leg.
2. Re-run §7.5a with a Sales voucher (Xyz Ltd Dr 100, Sales Cr 100)
   instead of the Receipt voucher.
3. Assert `GET /api/v1/reports/profit-loss?from_date=...&to_date=...`
   includes the 100 in `income.total` even while Tally is stopped.
4. Tick the §7.5a P&L checkbox in `VALIDATION_REPORT.md`.

**Owner / timing.** Not blocking Phase 0 closeout. Do during Phase
0.5 setup, or before the first customer onboards — whichever comes
first. Single-session task; ~30 minutes.
