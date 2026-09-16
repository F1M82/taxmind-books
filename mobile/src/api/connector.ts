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
  /**
   * The company to map into. Defaults to the currently-active company
   * (existing behavior). Pass this explicitly when attempting a mapping
   * against a company that ISN'T active yet — e.g. a candidate picked
   * from "Choose an existing company" — so the request targets it
   * without first flipping global active-company state (which would
   * remount the app stack via CompanyContext's activeCompanyVersion key
   * before this call's result is known, and any error would land on a
   * screen instance the user can no longer see).
   */
  companyId?: string,
): Promise<TallyMappingResponse> {
  return api.post<TallyMappingResponse>(
    "/api/v1/connector/tally-mapping",
    { discovery_id: discoveryId },
    companyId ? { companyId } : { withCompany: true },
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

// ---------------------------------------------------------------------
// Enrollment (owner-only — "Add a device")
// ---------------------------------------------------------------------

export interface EnrollmentCode {
  /** Raw one-time code. Shown once — the backend only stores its hash. */
  code: string;
  /** ISO-8601. 15 minutes from issue. */
  expires_at: string;
  company_id: string;
}

/**
 * Issue a one-time connector enrollment code for the active company.
 * Owner-only (backend returns 403 `insufficient_role` otherwise). The
 * teammate pastes the raw `code` into the connector `.exe`'s first-run
 * prompt (connector/connector/enrollment.py) — this call only produces
 * the code, it never talks to the connector itself.
 */
export async function issueEnrollmentCode(): Promise<EnrollmentCode> {
  return api.post<EnrollmentCode>(
    "/api/v1/connector/enrollment-codes",
    {},
    { withCompany: true },
  );
}
