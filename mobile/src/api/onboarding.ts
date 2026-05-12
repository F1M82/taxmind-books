/**
 * Typed bindings for /onboarding/checklist (P0.42).
 *
 * The backend uses `response_model_exclude_none=True`, so
 * `completed_at` is only present when `completed` is true. Optional
 * on the wire → optional in the type.
 */
import { api } from "./client";

export type OnboardingItemKey =
  | "company_created"
  | "connector_installed"
  | "ledgers_synced"
  | "first_voucher_posted"
  | "first_invoice_extracted";

export interface OnboardingItem {
  key: OnboardingItemKey;
  label: string;
  completed: boolean;
  completed_at?: string;
}

export interface OnboardingChecklistResponse {
  company_id: string;
  items: OnboardingItem[];
  completed_count: number;
  total_count: number;
}

export async function getOnboardingChecklist(): Promise<OnboardingChecklistResponse> {
  return api.get<OnboardingChecklistResponse>(
    "/api/v1/onboarding/checklist",
    { withCompany: true },
  );
}
