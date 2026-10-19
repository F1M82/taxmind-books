/** Typed bindings for /ingestions/*. All require X-Company-ID. */
import { api } from "./client";

export interface IngestionListItem {
  id: string;
  source: string;
  status: string;
  content_type: string | null;
  extraction_confidence: string | null;
  extraction_flags: string[];
  received_at: string;
}

export interface IngestionOut {
  id: string;
  company_id: string;
  source: string;
  status: string;
  content_type: string | null;
  content_size: number | null;
  received_at: string;
  extracted_at: string | null;
  failed_at: string | null;
  failure_reason: string | null;
  posted_voucher_id: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  parsed_data: Record<string, any> | null;
  extraction_confidence: string | null;
  extraction_flags: string[];
  created_at: string;
}

export interface IngestionListResponse {
  items: IngestionListItem[];
  meta: { next_cursor: string | null; total: number };
}

export async function listIngestions(params?: {
  status?: string;
}): Promise<IngestionListResponse> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  const suffix = qs.toString();
  return api.get<IngestionListResponse>(
    `/api/v1/ingestions/${suffix ? `?${suffix}` : ""}`,
    { withCompany: true },
  );
}

export async function getIngestion(id: string): Promise<IngestionOut> {
  return api.get<IngestionOut>(`/api/v1/ingestions/${id}`, {
    withCompany: true,
  });
}

export async function discardIngestion(
  id: string,
  reason?: string,
): Promise<IngestionOut> {
  return api.post<IngestionOut>(
    `/api/v1/ingestions/${id}/discard`,
    { reason: reason ?? null },
    { withCompany: true },
  );
}

export async function linkVoucherToIngestion(
  ingestionId: string,
  voucherId: string,
): Promise<IngestionOut> {
  return api.post<IngestionOut>(
    `/api/v1/ingestions/${ingestionId}/link-voucher`,
    { voucher_id: voucherId },
    { withCompany: true },
  );
}
