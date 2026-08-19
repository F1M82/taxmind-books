/**
 * Typed bindings for the /connector/* endpoints.
 *
 * Status + company mapping (P3.7 Phase 7B/7C). Sync is not surfaced
 * here — the mobile app never triggers `sync_masters`/ledger import;
 * it only reads mapping state and submits explicit operator mapping
 * confirmation.
 */
import { api } from "./client";

export interface ConnectorStatus {
  company_id: string;
  connector_id?: string;
  connected: boolean;
  last_seen_at: string | null;
  tally_running: boolean | null;
  tally_version: string | null;
  connector_version: string | null;
  queued_outbound_count: number | null;
}

export interface TallyCompanyDiscovery {
  discovery_id: string;
  tally_company_identifier: string;
  tally_company_name: string;
  /** Persisted discovery metadata; never render this in the mobile UI. */
  tally_master_id: string | null;
  gstin: string | null;
  financial_year_start: string | null;
  mapped_to_backend_company_id: string | null;
}

export interface TallyCompaniesResponse {
  connector_id: string;
  tally_data_folder_path: string | null;
  scanned_at: string | null;
  companies: TallyCompanyDiscovery[];
}

export interface TallyMappingResponse {
  company_id: string;
  connector_id: string;
  tally_data_folder_path: string;
  tally_company_identifier: string;
  tally_company_display_name: string;
  tally_mapping_configured_at: string;
  tally_mapping_configured_by: string | null;
}

export async function getConnectorStatus(): Promise<ConnectorStatus> {
  return api.get<ConnectorStatus>("/api/v1/connector/status", {
    withCompany: true,
  });
}

export async function getTallyCompanies(
  connectorId: string,
  refresh = false,
): Promise<TallyCompaniesResponse> {
  return api.get<TallyCompaniesResponse>(
    `/api/v1/connector/${connectorId}/tally-companies${refresh ? "?refresh=true" : ""}`,
    { withCompany: true },
  );
}

export async function mapTallyCompany(
  discoveryId: string,
): Promise<TallyMappingResponse> {
  return api.post<TallyMappingResponse>(
    "/api/v1/connector/tally-mapping",
    { discovery_id: discoveryId },
    { withCompany: true },
  );
}

// ---------------------------------------------------------------------
// Company ↔ Tally company mapping (P3.7 Phase 7B)
// ---------------------------------------------------------------------

export interface CompanyMappingStatus {
  company_id: string;
  /** The authoritative Tally company GUID (Company.tally_master_id). */
  tally_master_id: string | null;
  mapped: boolean;
}

export interface ConfirmCompanyMappingRequest {
  /** Tally company GUID — the authoritative identity, never inferred from a name. */
  tally_company_guid: string;
  /** Optional display name captured alongside the GUID. */
  tally_company_name?: string | null;
}

export interface ConfirmCompanyMappingResponse {
  company_id: string;
  tally_master_id: string;
  tally_company_name: string | null;
}

export async function getCompanyMapping(): Promise<CompanyMappingStatus> {
  return api.get<CompanyMappingStatus>(
    "/api/v1/connector/company-mapping",
    { withCompany: true },
  );
}

export async function confirmCompanyMapping(
  req: ConfirmCompanyMappingRequest,
): Promise<ConfirmCompanyMappingResponse> {
  return api.post<ConfirmCompanyMappingResponse>(
    "/api/v1/connector/company-mapping/confirm",
    req,
    { withCompany: true },
  );
}
