/**
 * Auth state for the entire app.
 *
 * Holds the current user (or null = signed out) and the boot-time
 * "loading" flag. Three actions:
 *   - signIn(email, password) — POSTs /auth/login + fetches /me
 *   - signUp(form) — POSTs /auth/register (no auto-login per Phase 0)
 *   - signOut() — clears tokens and resets state
 *
 * On mount, attempts to hydrate from a stored access token (silent
 * sign-in via /auth/me). If /me 401s the tokens are dropped.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  MeResponse,
  RegisterRequest,
  login as apiLogin,
  me as apiMe,
  register as apiRegister,
} from "../api/auth";
import {
  clearTokens,
  getAccessToken,
} from "../api/client";

interface AuthState {
  loading: boolean;
  user: MeResponse | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (form: RegisterRequest) => Promise<void>;
  signOut: () => Promise<void>;
  refreshMe: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | undefined>(undefined);

export function AuthProvider({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<MeResponse | null>(null);

  const hydrate = useCallback(async () => {
    const token = await getAccessToken();
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await apiMe());
    } catch {
      await clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await apiLogin(email, password);
      setUser(await apiMe());
    },
    [],
  );

  const signUp = useCallback(
    async (form: RegisterRequest) => {
      await apiRegister(form);
      // Phase 0: registration does NOT auto-login. Mobile flow is
      // register → success screen → user manually logs in.
    },
    [],
  );

  const signOut = useCallback(async () => {
    await clearTokens();
    setUser(null);
  }, []);

  const refreshMe = useCallback(async () => {
    setUser(await apiMe());
  }, []);

  const value = useMemo<AuthState>(
    () => ({ loading, user, signIn, signUp, signOut, refreshMe }),
    [loading, user, signIn, signUp, signOut, refreshMe],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (ctx === undefined) {
    throw new Error("useAuth must be used inside an <AuthProvider>");
  }
  return ctx;
}
