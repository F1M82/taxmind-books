/**
 * Typed bindings for /reports/*. All four endpoints require
 * `X-Company-ID` and follow the shapes in docs/REPORTS.md.
 */
import { api } from "./client";

export type DrCr = "Dr" | "Cr";
export type ProfitLossSign = "profit" | "loss";

// ---------- Trial Balance ----------

export interface TrialBalanceLedger {
  ledger_id: string;
  ledger_name: string;
  group_name: string | null;
  opening_balance: string;
  opening_balance_type: DrCr;
  period_dr: string;
  period_cr: string;
  closing_balance: string;
  closing_balance_type: DrCr;
}

export interface TrialBalanceResponse {
  as_of_date: string;
  company_id: string;
  ledgers: TrialBalanceLedger[];
  totals: { total_dr: string; total_cr: string; in_balance: boolean };
  exclusions: {
    optional_vouchers_excluded_count: number;
    cancelled_vouchers_excluded_count: number;
  };
}

export async function getTrialBalance(
  params: { as_of_date?: string } = {},
): Promise<TrialBalanceResponse> {
  const path = "/api/v1/reports/trial-balance" + qs(params);
  return api.get<TrialBalanceResponse>(path, { withCompany: true });
}

// ---------- Profit & Loss ----------

export interface PnLLedger {
  ledger_id: string;
  ledger_name: string;
  amount: string;
}

export interface PnLSection {
  ledgers: PnLLedger[];
  total: string;
}

/** Opening/closing stock folded into `net` (Dr-positive; a negative closing
 *  value is a net credit, i.e. Tally's negative stock). */
export interface PnLStock {
  opening_value: string;
  closing_value: string;
  source: "tally" | "manual";
  captured_at: string;
}

export interface ProfitLossResponse {
  from_date: string;
  to_date: string;
  income: PnLSection;
  expense: PnLSection;
  /** Present only when a stock valuation applies to this exact period. */
  stock?: PnLStock | null;
  net: { value: string; type: ProfitLossSign };
}

export async function getProfitLoss(
  params: { from_date?: string; to_date?: string } = {},
): Promise<ProfitLossResponse> {
  const path = "/api/v1/reports/profit-loss" + qs(params);
  return api.get<ProfitLossResponse>(path, { withCompany: true });
}

// ---------- Balance Sheet ----------

export interface BSLine {
  ledger_id: string;
  ledger_name: string;
  amount: string;
}

export interface BSGroup {
  group_name: string;
  ledgers: BSLine[];
  total: string;
}

export interface BSSection {
  groups: BSGroup[];
  total: string;
}

export interface BalanceSheetResponse {
  as_of_date: string;
  assets: BSSection;
  liabilities: BSSection;
  current_period_profit_loss: { value: string; type: ProfitLossSign };
  /** Net result of financial years before the one shown (Tally's P&L A/c
   *  "Opening Balance"). Optional: absent from older backends. */
  prior_periods_profit_loss?: { value: string; type: ProfitLossSign };
  /** True when the recorded stock valuation replaced the static stock ledger. */
  stock_applied?: boolean;
  equation: {
    assets: string;
    liabilities_plus_equity: string;
    in_balance: boolean;
  };
}

export async function getBalanceSheet(
  params: { as_of_date?: string } = {},
): Promise<BalanceSheetResponse> {
  const path = "/api/v1/reports/balance-sheet" + qs(params);
  return api.get<BalanceSheetResponse>(path, { withCompany: true });
}

// ---------- Outstanding ----------

export type OutstandingType = "receivables" | "payables";

export interface OutstandingItem {
  ledger_id: string;
  ledger_name: string;
  ledger_gstin: string | null;
  balance: string;
  balance_type: DrCr;
}

export interface OutstandingResponse {
  type: OutstandingType;
  as_of_date: string;
  items: OutstandingItem[];
  total: string;
  total_type: DrCr;
}

export async function getOutstanding(params: {
  type: OutstandingType;
  as_of_date?: string;
}): Promise<OutstandingResponse> {
  const path = "/api/v1/reports/outstanding" + qs(params);
  return api.get<OutstandingResponse>(path, { withCompany: true });
}

// ---------- helpers ----------

function qs(params: Record<string, string | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  return parts.length === 0 ? "" : `?${parts.join("&")}`;
}

// ---- Financial-year periods (picker) ----

export interface FinancialYear {
  /** e.g. "FY 2025-26" */
  label: string;
  from_date: string;
  /** FY end, or today for the current year. */
  to_date: string;
  is_current: boolean;
}

export interface ReportPeriodsResponse {
  items: FinancialYear[];
}

/** Financial years the company has data for, newest first. */
export async function getReportPeriods(): Promise<ReportPeriodsResponse> {
  return api.get<ReportPeriodsResponse>("/api/v1/reports/periods");
}
