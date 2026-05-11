/** Typed bindings for /companies/* endpoints. */
import { api } from "./client";

export interface CompanyListItem {
  id: string;
  name: string;
  gstin: string | null;
  status: string;
  your_role: string;
}

export interface CompanyOut {
  id: string;
  name: string;
  gstin: string | null;
  pan: string | null;
  financial_year_start: string;
  status: string;
  address: string | null;
  city: string | null;
  state_code: string | null;
  pincode: string | null;
  accounting_source: string;
  created_at: string;
  your_role: string;
}

export interface CompanyListResponse {
  items: CompanyListItem[];
  meta: { next_cursor: string | null; total: number };
}

export interface CompanyCreateRequest {
  name: string;
  gstin?: string | null;
  pan?: string | null;
  financial_year_start?: string | null;
  address?: string | null;
  city?: string | null;
  state_code?: string | null;
  pincode?: string | null;
  accounting_source?: string;
}

export async function listCompanies(): Promise<CompanyListResponse> {
  return api.get<CompanyListResponse>("/api/v1/companies/");
}

export async function createCompany(
  req: CompanyCreateRequest,
): Promise<CompanyOut> {
  return api.post<CompanyOut>("/api/v1/companies/", req);
}
