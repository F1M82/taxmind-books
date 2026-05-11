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
