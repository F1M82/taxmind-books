/** Typed bindings for /dashboard/home (P0.40). Requires X-Company-ID. */
import { api } from "./client";

export type AlertSeverity = "info" | "warning" | "critical";
export type AlertKind =
  | "connector_offline"
  | "connector_stale"
  | "tally_not_running"
  | "pending_approvals";

export interface DashboardConnector {
  connected: boolean;
  tally_running: boolean | null;
  last_seen_seconds_ago: number | null;
}

export interface DashboardTodayMetrics {
  vouchers_created: number;
  vouchers_pending_approval: number;
  cash_in: string;
  cash_out: string;
}

export interface DashboardMonthMetrics {
  cash_in: string;
  cash_out: string;
  vouchers_created: number;
  vouchers_pending_approval: number;
}

export interface DashboardOutstanding {
  receivables_total: string;
  payables_total: string;
}

export interface DashboardGstLiability {
  month_to_date: string;
}

export interface DashboardAlert {
  kind: AlertKind;
  severity: AlertSeverity;
  message: string;
  since: string | null;
}

export interface DashboardHomeResponse {
  as_of: string;
  company_name: string;
  connector: DashboardConnector;
  today: DashboardTodayMetrics;
  this_month: DashboardMonthMetrics;
  outstanding: DashboardOutstanding;
  gst_liability_indicative: DashboardGstLiability;
  alerts: DashboardAlert[];
}

export async function getDashboardHome(): Promise<DashboardHomeResponse> {
  return api.get<DashboardHomeResponse>("/api/v1/dashboard/home", {
    withCompany: true,
  });
}
