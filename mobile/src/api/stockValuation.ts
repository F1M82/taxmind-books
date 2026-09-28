/** Typed bindings for the per-financial-year stock valuation endpoints. */
import { api } from "./client";

export interface StockValuation {
  id: string;
  /** e.g. "FY 2025-26" */
  label: string;
  period_from: string;
  period_to: string;
  /** Dr-positive money string. Negative = net credit (Tally's negative stock). */
  opening_value: string;
  closing_value: string;
  source: "tally" | "manual";
  item_count: number | null;
  /** Items with a NEGATIVE closing quantity (issued more than received). */
  negative_stock_items: number | null;
  captured_at: string;
}

export interface StockValuationListResponse {
  items: StockValuation[];
}

/** Recorded valuations, newest financial year first. */
export async function listStockValuations(): Promise<StockValuationListResponse> {
  return api.get<StockValuationListResponse>("/api/v1/stock-valuations", {
    withCompany: true,
  });
}

/**
 * Ask the connected Tally for its stock value and record it for the
 * financial year Tally's period is set to (owner/admin).
 */
export async function pullStockValuation(
  companyId: string,
): Promise<StockValuation> {
  return api.post<StockValuation>(
    `/api/v1/connector/stock-valuation/${companyId}`,
    {},
    { withCompany: true },
  );
}
