/** Typed bindings for the /auth/* endpoints. */
import { api, setTokens } from "./client";

export interface RegisterRequest {
  email: string;
  password: string;
  full_name: string;
  phone?: string | null;
  is_ca?: boolean;
  firm_name?: string | null;
  ca_membership_no?: string | null;
}

export interface UserOut {
  id: string;
  email: string;
  full_name: string;
  is_ca: boolean;
  firm_name: string | null;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: {
    id: string;
    email: string;
    full_name: string;
    is_ca: boolean;
    firm_name: string | null;
  };
}

export interface MeResponse {
  id: string;
  email: string;
  full_name: string;
  is_ca: boolean;
  firm_name: string | null;
  is_active: boolean;
  companies: { id: string; name: string; role: string }[];
}

export async function register(req: RegisterRequest): Promise<UserOut> {
  return api.post<UserOut>("/api/v1/auth/register", req);
}

/**
 * OAuth2 password flow — form-encoded, not JSON. The generic
 * `api.post` wrapper sends JSON, so we hand-craft this one.
 */
export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const body = new URLSearchParams({
    username: email,
    password,
  }).toString();
  const { baseUrl } = await import("./client");
  const resp = await fetch(`${baseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!resp.ok) {
    const text = await resp.text();
    let body: unknown = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      // leave body as {}
    }
    const { ApiError } = await import("./client");
    throw new ApiError(resp.status, body as never);
  }
  const tokens = (await resp.json()) as TokenResponse;
  await setTokens(tokens.access_token, tokens.refresh_token);
  return tokens;
}

export async function me(): Promise<MeResponse> {
  return api.get<MeResponse>("/api/v1/auth/me");
}
