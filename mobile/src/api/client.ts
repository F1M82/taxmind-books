/**
 * HTTP client used by every screen.
 *
 * Reads the API base URL from Expo's runtime config (`extra` in
 * app.json) so the same binary works against local dev, staging,
 * and production by re-publishing the OTA bundle with a different
 * config.
 *
 * Refreshes the access token automatically on 401 — see
 * `refreshAccessToken()` and the `request()` retry path.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";

const KEY_ACCESS = "tmb.access_token";
const KEY_REFRESH = "tmb.refresh_token";
const KEY_COMPANY = "tmb.active_company_id";

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown };
  request_id: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId: string;
  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.status = status;
    this.code = body.error.code;
    this.details = body.error.details;
    this.requestId = body.request_id;
  }
}

function baseUrl(): string {
  const extra = (Constants.expoConfig?.extra ?? {}) as Record<
    string,
    string | undefined
  >;
  const url = extra.API_BASE_URL;
  if (!url) {
    throw new Error(
      "API_BASE_URL missing from expo config (app.json extra)",
    );
  }
  return url.replace(/\/+$/, "");
}

export async function getAccessToken(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_ACCESS);
}

export async function getRefreshToken(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_REFRESH);
}

export async function setTokens(
  access: string,
  refresh: string,
): Promise<void> {
  await AsyncStorage.multiSet([
    [KEY_ACCESS, access],
    [KEY_REFRESH, refresh],
  ]);
}

export async function clearTokens(): Promise<void> {
  await AsyncStorage.multiRemove([KEY_ACCESS, KEY_REFRESH, KEY_COMPANY]);
}

export async function getActiveCompanyId(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_COMPANY);
}

export async function setActiveCompanyId(id: string): Promise<void> {
  await AsyncStorage.setItem(KEY_COMPANY, id);
}

interface RequestInit {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  /**
   * When true, this request implicitly requires the active company
   * (`X-Company-ID` header). The client reads it from storage and
   * raises if missing — surfaces a bug rather than a 422 from the
   * server.
   */
  withCompany?: boolean;
  /** Internal: set when we're already retrying after a 401 refresh. */
  _isRetry?: boolean;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = `${baseUrl()}${path}`;
  const access = await getAccessToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers ?? {}),
  };
  if (access) {
    headers["Authorization"] = `Bearer ${access}`;
  }
  if (init.withCompany) {
    const companyId = await getActiveCompanyId();
    if (!companyId) {
      throw new Error("active company is required but not set");
    }
    headers["X-Company-ID"] = companyId;
  }

  const response = await fetch(url, {
    method: init.method ?? "GET",
    headers,
    body:
      init.body === undefined || init.method === "GET"
        ? undefined
        : JSON.stringify(init.body),
  });

  if (response.status === 401 && !init._isRetry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, { ...init, _isRetry: true });
    }
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const json = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new ApiError(response.status, json as ApiErrorBody);
  }
  return json as T;
}

async function refreshAccessToken(): Promise<boolean> {
  const refresh = await getRefreshToken();
  if (!refresh) {
    return false;
  }
  try {
    const resp = await fetch(`${baseUrl()}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!resp.ok) {
      await clearTokens();
      return false;
    }
    const json = (await resp.json()) as {
      access_token: string;
      refresh_token: string;
    };
    await setTokens(json.access_token, json.refresh_token);
    return true;
  } catch {
    await clearTokens();
    return false;
  }
}

export const api = {
  get: <T>(path: string, init: Omit<RequestInit, "method" | "body"> = {}) =>
    request<T>(path, { ...init, method: "GET" }),
  post: <T>(
    path: string,
    body: unknown,
    init: Omit<RequestInit, "method" | "body"> = {},
  ) => request<T>(path, { ...init, method: "POST", body }),
  patch: <T>(
    path: string,
    body: unknown,
    init: Omit<RequestInit, "method" | "body"> = {},
  ) => request<T>(path, { ...init, method: "PATCH", body }),
  delete: <T>(
    path: string,
    init: Omit<RequestInit, "method" | "body"> = {},
  ) => request<T>(path, { ...init, method: "DELETE" }),
};

export { baseUrl, refreshAccessToken };
