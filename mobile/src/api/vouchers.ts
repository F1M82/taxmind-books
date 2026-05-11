/** Typed bindings for /vouchers/*. All require X-Company-ID. */
import { api } from "./client";

export type VoucherType =
  | "Receipt"
  | "Payment"
  | "Sales"
  | "Purchase"
  | "Journal"
  | "Contra"
  | "Debit Note"
  | "Credit Note";

export interface VoucherEntryCreate {
  ledger_id: string;
  amount: string;
  entry_type: "Dr" | "Cr";
}

export interface VoucherCreateRequest {
  voucher_type: VoucherType;
  date: string;
  narration?: string | null;
  reference?: string | null;
  total_amount: string;
  entries: VoucherEntryCreate[];
  gst_applicable?: boolean;
  place_of_supply?: string | null;
}

export interface VoucherListItem {
  id: string;
  voucher_type: string;
  voucher_number: string | null;
  date: string;
  narration: string | null;
  reference: string | null;
  total_amount: string;
  status: string;
  source: string;
  gst_applicable: boolean;
  tally_posted_at: string | null;
  created_at: string;
}

export interface VoucherListResponse {
  items: VoucherListItem[];
  meta: { next_cursor: string | null; total: number };
}

export async function listVouchers(): Promise<VoucherListResponse> {
  return api.get<VoucherListResponse>("/api/v1/vouchers/", {
    withCompany: true,
  });
}

export async function createVoucher(
  req: VoucherCreateRequest,
  idempotencyKey: string,
): Promise<VoucherListItem> {
  return api.post<VoucherListItem>("/api/v1/vouchers/", req, {
    withCompany: true,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}
