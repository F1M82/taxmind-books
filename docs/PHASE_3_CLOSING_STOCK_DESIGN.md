# Phase 3 — Stock valuation in P&L / Balance Sheet (PROPOSED)

**Status:** PROPOSED 2026-09-28. Design only — no code, no schema change, nothing
deployed. Needs approval before any build. Follows
`PHASE_3_OPENING_BALANCE_ARCHITECTURE.md` (the opening-balance seed) and the
one-off "Opening Stock" ledger created on prod for Vighnaharta on 2026-09-28.

---

## The problem

TaxMind's P&L is `Σ income-group movements − Σ expense-group movements`
(`services/reporting/profit_loss.py`). Tally's trading account also carries
**opening stock (Dr)** and **closing stock (Cr)**, both computed from inventory,
not from ledger vouchers. The backend has no inventory model, so neither reaches
the P&L.

State after 2026-09-28 (Vighnaharta, prod):

- The trial balance now balances: a books-only `Opening Stock` ledger
  (group `Stock-in-hand`, Dr 7,33,801.87) stands in for the 36 opening stock items.
- That ledger is a static asset. The P&L still omits opening stock (should be a
  cost) and closing stock (should be a credit), and the balance sheet shows stock
  at its opening value forever.
- Net effect: reported profit is off by `opening stock − closing stock`.

Live Tally probe 2026-09-28 (210 stock items, 36 with an opening value):
`Σ OPENINGVALUE = 7,33,801.87 (Dr)`; `Σ CLOSINGVALUE = 1,39,818.21` for
Tally's current view. **Unverified:** the sign convention and the period basis of
`CLOSINGVALUE` (Tally signs opening debits negative; closing came back positive).
This must be pinned down with explicit `SVFROMDATE`/`SVTODATE` before any code.

## Options

**A. Period-end stock-adjustment vouchers (books-only journal).** Post
`Dr Closing Stock / Cr Trading` and reverse the opening at period end.
Rejected: these would have to stay out of Tally (Tally derives stock itself), which
collides with the existing dispatch/sync model and the "cancel is books-only"
constraint, and it makes every report depend on someone remembering to post it.

**B. Valuation snapshots, applied in the report engine. (Recommended.)**
Tally stays the source of truth for stock. The backend stores dated stock
*values* only (no quantities, rates or items) and the report engine folds them in:

- New table `stock_valuations(company_id, as_of_date, value, source, synced_at)`,
  one row per (company, date), append-only, value in the backend's Dr-positive
  convention.
- New read-only connector command `get_stock_valuation(as_of_date)` — a TDL
  collection over `StockItem` summing `ClosingValue` at that date (same pattern as
  `get_ledger_opening_balances`; must negate/normalise sign like the seed does).
- P&L for `[from, to]`: add an **Opening Stock** expense line = valuation at
  `from − 1 day` (for the first FY on the anchor: the seeded opening value), and a
  **Closing Stock** income line = valuation at `to`. Both computed, never stored as
  ledger movements.
- Balance sheet at `as_of`: the `Stock-in-hand` figure = valuation at `as_of`
  (replacing the static ledger's balance so stock is not counted twice); the current
  period's profit picks up the same lines, so the sheet still balances.
- Trial balance: **unchanged.** The `Opening Stock` ledger keeps the anchor TB
  honest; TB is a ledger report, not a trading account.
- Missing valuation for a needed date ⇒ the report says so explicitly
  (`stock_valuation_missing`) rather than silently assuming zero.

**C. Full inventory model** (stock items, quantities, rates, movements, valuation
method). Correct long term, weeks of work, and duplicates what Tally already does.
Deferred to Phase B / a real customer need.

## Why B

- Smallest thing that makes P&L and BS right for the pilot.
- Keeps Tally as the single source of truth (consistent with the pull-direction
  decision for masters and the opening-balance seed).
- No vouchers created, so nothing to sync back and nothing to cancel.
- Reversible: dropping the table and the two report lines restores today's behaviour.

## Open questions (resolve before building)

1. `CLOSINGVALUE` sign + period semantics via explicit `SVFROMDATE`/`SVTODATE`;
   confirm the figure for a FY-end date against Tally's own Balance Sheet
   "Closing Stock" line for that date, to the paisa.
2. Tally valuation method per item (FIFO / avg / last purchase) is Tally's
   choice; we mirror its number and never recompute.
3. When to snapshot: on each auto-sync, on demand, or per FY-end only.
   Suggested: on demand + at each sync, keeping the last value per date.
4. Behaviour for companies with no stock at all (valuation absent ⇒ no lines,
   not an error).
5. The `Opening Stock` ledger and the BS `Stock-in-hand` replacement must not
   double count — needs an explicit test on a company that has both.

## Constraints found reading the report engine (2026-09-28)

- **BS in-balance invariant.** `compute_balance_sheet` asserts
  `assets == liabilities + current-FY P&L`. If the BS swaps the static
  `Opening Stock` ledger for `valuation(as_of)` *and* the P&L gains
  `+closing − opening`, the sheet still balances only when
  `opening valuation used by the P&L == the seeded ledger balance`
  (true for the anchor FY: `valuation(anchor − 1 day)` must equal the seeded
  Dr 7,33,801.87). Enforce this: seed a `stock_valuations` row at
  `anchor − 1 day` from the same Tally `OPENINGVALUE` sum, and test that a
  mismatch is surfaced, not papered over.
- **Later FYs are already incomplete.** The BS only folds in the *current* FY's
  P&L; prior-year profit is expected to arrive via capital/reserve openings, which
  the single-anchor design does not roll forward. Stock valuation inherits that
  limitation; it does not fix it. Multi-FY balance-sheet correctness is a separate
  item and should not be bundled into this change.
- **API shape.** `PnLLedgerLine` carries a `ledger_id: UUID`. The synthetic
  Opening/Closing Stock lines have no ledger, so the response schema (and the
  mobile P&L screen that renders it) needs either a nullable `ledger_id` or a
  separate `stock` block. Additive, but it touches `docs/API.md`, the OpenAPI
  reference and the mobile client, so it is more than a backend-only change.
- **Blast radius.** `compute_profit_loss` also feeds the dashboard's net profit
  (`compute_dashboard_financials`), so every consumer moves at once when a
  valuation row first appears. With no valuation rows, behaviour must stay
  byte-identical to today (test this first).

## Build order once the probe is done

1. Connector `get_stock_valuation` + fake-Tally tests (sign per the probe).
2. Migration + model `stock_valuations` (append-only), service to record/read.
3. Seed the `anchor − 1` row; wire an on-demand pull (and optionally auto-sync).
4. P&L lines + BS stock swap behind "valuation rows exist", with the
   no-valuation-rows-means-unchanged regression test written before the feature.
5. API schema/OpenAPI/docs, then mobile rendering.

## Out of scope

Quantities, item masters, stock vouchers, godowns, batch tracking, GST HSN
reporting from stock, any write to Tally.
