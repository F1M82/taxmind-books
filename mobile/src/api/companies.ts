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

// ---- Members (admin surface) ----

export type MemberRole = "owner" | "admin" | "accountant" | "viewer";

export const MEMBER_ROLES: MemberRole[] = [
  "owner",
  "admin",
  "accountant",
  "viewer",
];

export interface Member {
  id: string;
  user_id: string;
  company_id: string;
  role: string;
  user_email: string;
  created_at: string;
}

export interface MemberListResponse {
  items: Member[];
}

export async function listMembers(
  companyId: string,
): Promise<MemberListResponse> {
  return api.get<MemberListResponse>(
    `/api/v1/companies/${companyId}/members`,
  );
}

export async function addMember(
  companyId: string,
  email: string,
  role: MemberRole,
): Promise<Member> {
  return api.post<Member>(`/api/v1/companies/${companyId}/members`, {
    email,
    role,
  });
}

export async function setMemberRole(
  companyId: string,
  userId: string,
  role: MemberRole,
): Promise<Member> {
  return api.patch<Member>(
    `/api/v1/companies/${companyId}/members/${userId}`,
    { role },
  );
}

export async function removeMember(
  companyId: string,
  userId: string,
): Promise<void> {
  return api.delete<void>(
    `/api/v1/companies/${companyId}/members/${userId}`,
  );
}
