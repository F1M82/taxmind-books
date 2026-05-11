/** Typed bindings for the /connector/* endpoints (status only — sync
 *  is owner-tooling and lives on a dedicated settings screen later).
 */
import { api } from "./client";

export interface ConnectorStatus {
  company_id: string;
  connected: boolean;
  last_seen_at: string | null;
  tally_running: boolean | null;
  tally_version: string | null;
  connector_version: string | null;
  queued_outbound_count: number | null;
}

export async function getConnectorStatus(): Promise<ConnectorStatus> {
  return api.get<ConnectorStatus>("/api/v1/connector/status", {
    withCompany: true,
  });
}
