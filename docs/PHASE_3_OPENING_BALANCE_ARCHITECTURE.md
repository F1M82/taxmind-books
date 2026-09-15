# Phase 3 — Opening-Balance Architecture (P3.2, APPROVED)

**Status:** APPROVED 2026-08-14. Design record only — **no schema change**, no
code change, nothing committed/deployed. Governs the historical Tally mirror
(Phase 3). Supersedes any notion of per-financial-year opening-balance fields.

---

## The decision (authoritative)

1. **`ledgers.opening_balance` is the balance at the migration _anchor date_** —
   the start of the **earliest imported financial year**. It is a single value
   per ledger (magnitude in `opening_balance`, side in `balance_type`).
2. It is **established once** by an **idempotent** migration/seed operation.
3. It is **NEVER overwritten** with each financial year's Tally opening balance.
   Re-setting it per FY corrupts every other FY's reports (see "Why" below).
4. **Later FY openings are DERIVED**, not stored:
   `opening(FY) = anchor_opening + Σ(dated movements before FY start)`.
5. The existing **TB / P&L / Balance-Sheet report engines remain the source of
   truth** for every period; nothing about report computation changes.
6. **No FY-opening schema or table is required for correctness.**
7. A FY-opening **snapshot/cache** may be introduced **later, only if
   measurement justifies it** (large-data performance, or a demand for frozen
   year-end figures). If introduced, it is a *derived accelerator*, never the
   source of truth.

---

## Why a single field is correct (and per-FY mutation is wrong)

In double-entry with a complete transaction history you need exactly **one**
stored opening per ledger: the balance at the anchor date. Every later balance
is reconstructable from that anchor plus dated movements. This is already how
the report engine computes balances:

- **Trial balance** — `compute_trial_balance(as_of_date)` returns
  `opening_signed + Σ(movements with date ≤ as_of)`
  (`backend/app/services/reporting/trial_balance.py`, `_period_movement_subquery`
  + `_ledger_balance_signed`). Cumulative ⇒ any FY's closing.
- **P&L** — `compute_profit_loss(from_date, to_date)` is a **period** report;
  it sums only movements in `[from_date, to_date]`
  (`backend/app/services/reporting/profit_loss.py`). ⇒ any FY's result directly.
- **Balance sheet** — `compute_balance_sheet(as_of_date)` is as-of, and already
  bounds its current-period P&L with `fiscal_year_start(as_of_date)`
  (`backend/app/services/reporting/balance_sheet.py`). ⇒ per-FY presentation,
  with prior years carried in capital/reserves openings, exactly like Tally.

**The trap:** if migration set `opening_balance` to *each FY's* Tally opening as
it imported, then after importing an earlier FY's transactions the trial balance
would double-count (opening already includes those movements). Therefore the
field must be anchored **once** at the earliest imported FY and never re-written.

---

## Worked example — ledger "HDFC BANK", anchor = 2023-04-01

Stored once: `opening_balance = ₹1,00,000`, `balance_type = Dr` (immutable).

| FY | Net movement (imported vouchers) | Derived opening (= prior close) | Engine closing (`opening + Σ ≤ date`) |
|---|---|---|---|
| 2023-24 | +₹50,000 | ₹1,00,000 (as-of 2023-03-31) | **₹1,50,000** (as-of 2024-03-31) |
| 2024-25 | +₹30,000 | ₹1,50,000 (as-of 2024-03-31) | **₹1,80,000** (as-of 2025-03-31) |
| 2025-26 | −₹20,000 | ₹1,80,000 (as-of 2025-03-31) | **₹1,60,000** (as-of 2026-03-31) |

Each FY's opening equals the prior FY's closing, and all of it derives from the
one stored anchor + dated transactions. TB (as-of), P&L (period), and BS (as-of)
reproduce for **every** FY with no per-FY field and **no schema change**.

---

## Seeding operation (design intent, not yet built)

- Compute each ledger's anchor opening from Tally (e.g. a Trial Balance as-of
  `anchor_date − 1 day`, mapped signed → magnitude + `Dr`/`Cr`).
- Write `opening_balance` / `balance_type` **once** per ledger for the run;
  idempotent (re-running with the same anchor is a no-op).
- Record the anchor date and prior values in the migration run manifest so the
  seed is reversible (rollback restores prior openings).

## Explicitly out of scope for P3.2

- No migration executed. No opening balances mutated. No vouchers ingested.
- No change to the `vouchers` / `ledgers` schema or to any constraint.
- The `uq_vouchers_company_number_type` decision is deferred to real-company
  evidence (P3.7 gate); not changed on assumption.
