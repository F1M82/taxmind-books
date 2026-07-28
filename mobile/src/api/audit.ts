/** Typed bindings for /audit-logs/* (admin surface — activity log). */
import { api } from "./client";

export interface AuditLogItem {
  id: string;
  user_id: string | null;
  user_email: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  ip_address: string | null;
  source: string;
  created_at: string;
}

export interface AuditLogListResponse {
  items: AuditLogItem[];
  meta: { next_cursor?: string | null; total?: number | null };
}

export interface AuditLogQuery {
  action?: string;
  from?: string;
  to?: string;
  limit?: number;
}

export async function listAuditLogs(
  query: AuditLogQuery = {},
): Promise<AuditLogListResponse> {
  const params = new URLSearchParams();
  if (query.action) params.set("action", query.action);
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  params.set("limit", String(query.limit ?? 50));
  return api.get<AuditLogListResponse>(
    `/api/v1/audit-logs/?${params.toString()}`,
    { withCompany: true },
  );
}
