/** Typed bindings for /ledgers/*. All require X-Company-ID. */
import { api } from "./client";

export interface LedgerListItem {
  id: string;
  name: string;
  group_name: string | null;
  opening_balance: string;
  balance_type: "Dr" | "Cr";
  gstin: string | null;
  is_active: boolean;
  created_via_mobile: boolean;
  confirmed_in_tally_at: string | null;
}

export interface LedgerCreateRequest {
  name: string;
  group_name?: string | null;
  opening_balance?: string;
  balance_type?: "Dr" | "Cr";
  gstin?: string | null;
}

export interface LedgerListResponse {
  items: LedgerListItem[];
  meta: { next_cursor: string | null; total: number };
}

export async function listLedgers(params?: {
  q?: string;
  group?: string;
}): Promise<LedgerListResponse> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.group) qs.set("group", params.group);
  const suffix = qs.toString();
  return api.get<LedgerListResponse>(
    `/api/v1/ledgers/${suffix ? `?${suffix}` : ""}`,
    { withCompany: true },
  );
}

/**
 * Creates a ledger. Always lands `created_via_mobile=true` on the
 * backend (v1.3 item 7) -- the connector pushes it to Tally in the
 * background, and any voucher referencing it before Tally confirms is
 * forced Optional, regardless of confidence.
 */
export async function createLedger(
  req: LedgerCreateRequest,
): Promise<LedgerListItem> {
  return api.post<LedgerListItem>("/api/v1/ledgers/", req, {
    withCompany: true,
  });
}
