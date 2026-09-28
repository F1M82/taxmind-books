# Phase 3 — Stock valuation in P&L / Balance Sheet (IMPLEMENTED, not deployed)

**Status:** APPROVED and BUILT 2026-09-28 (Option B, revised per the probe
results below) — see "As built" at the end. Not deployed to prod: needs migration
`0021`, the backend files, and a rebuilt connector `.exe`. Follows
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

## Probe results (live Tally, Vighnaharta, 2026-09-28)

Resolved:

- **Sign.** Tally signs debits NEGATIVE in opening *and* closing alike (checked
  against group totals: Sundry Debtors −39,06,266.58 → −42,09,599.27, Sundry
  Creditors positive). Stock follows it, so backend value = `−tally_value`
  (Dr-positive), same negation as the opening-balance seed.
- **Group cross-check.** The `Stock-in-Hand` group balance equals
  `Σ StockItem OPENINGVALUE/CLOSINGVALUE` exactly (−7,33,801.87 opening;
  +1,39,818.21 closing), so the item sum is Tally's own stock figure.
- **Closing stock is a net CREDIT (+1,39,818.21 in Tally sign ⇒ −1,39,818.21 in
  backend convention).** 21 items close Cr (7,84,550.01), 18 close Dr
  (6,44,731.80); many have **negative closing quantities** (e.g. KINGKONG 250 ML
  −206 NOS): stock was sold without matching purchase receipts in inventory.
  This is a **Tally data-quality issue**, not a sign bug. The valuation must
  mirror Tally faithfully (**store signed, never clamp at zero**), and the bookkeeper
  should be told about the negative-stock items.

NOT resolved — and it changes the design:

- **`SVFROMDATE`/`SVTODATE` static variables on `StockItem` / `Group`
  collections hang TallyPrime here.** Both timed out (30–240 s) and froze the UI;
  the same collections *without* them answer in ~0.1 s. **Correction
  (2026-09-28, later):** this is NOT proof that all date-scoping fails. The
  project's own P3.0 spike (see `_build_get_vouchers_xml`) found that dates only
  take effect as **literal values inside a TDL `FILTER`**; `@@SVFromDate` /
  static variables do not resolve in a collection. My probe used the wrong form,
  so the hang is most likely self-inflicted, not a Tally limitation. Whether a
  *valuation* (as opposed to a voucher `Date`) can be scoped by a literal filter
  is still unknown, because stock value at a date is computed by Tally, not a
  stored field a filter can select.
- Consequence: a `get_stock_valuation(as_of_date)` command via variables is not
  viable. Two routes remain: (1) the safe **current** value (no variables),
  snapshotted and labelled with its date; (2) compute valuation ourselves from
  voucher inventory lines fetched through the proven literal-filter voucher export.
  Reports must say when a needed date has no snapshot
  (`stock_valuation_missing`) rather than interpolate.
- **Loaded-company facts (Tally, 2026-09-28):** `STARTINGFROM = BOOKSFROM =
  2025-04-01`, `LASTVOUCHERDATE = 2026-07-21`, i.e. about 1.3 years, not 3. Three
  literal-filter voucher windows inside that span (Apr 2025, Mar 2026, Jul 2026)
  all returned 0 vouchers although vouchers exist, so the period selected in the
  Tally window probably scopes what a collection can see. Unresolved.
- Still to determine, safely: what period the no-date figure represents (books
  start → last voucher date, or Tally's current date). Decide by comparing
  against Tally's own Balance Sheet on screen, not by more date-ranged queries.

## RESOLUTION of the gateway/period mystery (2026-09-28, verified live)

**The XML gateway is scoped by the company-level period set from the Gateway of
Tally main menu (F2 there) — not by a period changed inside an open report, and
not by anything the request says.** Verified against the on-screen Balance Sheet
for 31-Mar-26: with the main-menu period on FY 2025-26 the plain (no date
variable) group query returned Stock-in-Hand closing +1,39,818.21 (Tally sign;
screen shows (−)1,39,818.21), Bank −28,330.64, Cash +40,518.00, Debtors
−42,09,599.27 — all matching the screen — and the connector's literal-filter
`get_vouchers` returned 21 vouchers for Apr-2025 and 36 for Mar-2026. While the
period had been changed only inside a report window, the same queries returned
closing == opening and zero vouchers. My earlier "SVFROMDATE persists" hypothesis
was wrong; the static variables did cause the freezes (StockItem/Group valuation
with them), but the empty results were the period scope.

Consequences for the design:

1. **Per-period stock via the operator is viable and needs no risky queries.**
   The operator selects the period at the Gateway of Tally main menu; the connector
   reads Tally's own Opening/Closing stock with the plain query; the backend stores
   the pair against that period (`period_start`, `period_end`, `opening_value`,
   `closing_value`, `captured_at`). Repeat once per financial year.
2. **Import must verify the period, not assume it.** Before reading any window the
   connector must confirm Tally's active period covers it (read the company's
   period), and refuse with a clear message ("set the period to FY xx-yy at the
   Gateway of Tally") instead of returning an empty list that looks like "no
   vouchers". The current `get_vouchers` silently returns `[]` — a data-loss trap
   for a bulk import.
3. **Never send `SVFROMDATE`/`SVTODATE` to this Tally.**

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

---

## As built (2026-09-28)

**Table** `stock_valuations` (migration `0021`): one row per (company, financial
year), `opening_value` / `closing_value` signed Dr-positive, `source` tally|manual,
`item_count` / `negative_stock_items` (data quality, Tally pulls only).

**Recording** (`services/stock_valuation_service.py`):
- exactly one Indian FY (1 Apr–31 Mar), else `422 stock_period_not_single_fy`;
- for the company's first FY the opening must equal the Stock-in-Hand ledger
  balance, else `409 stock_opening_mismatch` (keeps the balance sheet balanced);
- re-recording a year replaces it; audited (`stock_valuation.recorded`).

**Sources:** `POST /connector/stock-valuation/{company_id}` (connector command
`get_stock_valuation`: plain StockItem query, no date variables; fail-closed company
mapping) and `PUT /stock-valuations/{period_from}` (manual). `GET /stock-valuations`
lists. Mobile: Dashboard -> "Stock valuation" (owner/admin pull button, negative-stock
warning).

**Reports** (`services/reporting/stock.py`): applied only for whole-year windows with
the needed valuations; balance sheet all-or-nothing and only if it still balances;
no valuation => reports unchanged. See `REPORTS.md`.

**Operator runbook, per financial year:** in Tally, at the *Gateway of Tally main menu*
press F2 and set the period to that one year -> in the app open Stock valuation ->
"Read stock from Tally". Repeat for each year (first year first).

**Not built:** manual-entry UI on mobile (API only), per-item quantities/rates, a
Trial Balance stock line (the TB stays a ledger report; the seeded `Opening Stock`
ledger keeps the opening TB honest), automatic capture on each sync.
