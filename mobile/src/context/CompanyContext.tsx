/**
 * Holds the active company id. Persisted in AsyncStorage so the
 * X-Company-ID header survives app restarts.
 *
 * On login we attempt to pick a sensible default:
 *   - If there's a stored id that's still a valid membership → keep it.
 *   - Else pick the first company in the /me companies list.
 *   - Else null (the user has no companies — the create-company screen
 *     handles this gracefully).
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
  getActiveCompanyId,
  setActiveCompanyId,
} from "../api/client";
import { useAuth } from "./AuthContext";

interface CompanyState {
  activeCompanyId: string | null;
  loading: boolean;
  /** Changes whenever the active tenant changes, for screen cache invalidation. */
  activeCompanyVersion: number;
  setActive: (id: string) => Promise<void>;
  /** Force a re-resolve from AuthContext (e.g. after creating a company). */
  reconcile: () => Promise<void>;
}

const CompanyCtx = createContext<CompanyState | undefined>(undefined);

export function CompanyProvider({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  const { user, loading: authLoading } = useAuth();
  const [activeCompanyId, setActiveState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeCompanyVersion, setActiveCompanyVersion] = useState(0);

  const reconcile = useCallback(async () => {
    if (user === null) {
      setActiveState(null);
      setActiveCompanyVersion((version) => version + 1);
      setLoading(false);
      return;
    }
    const stored = await getActiveCompanyId();
    const membership = user.companies.find((c) => c.id === stored);
    if (stored !== null && membership !== undefined) {
      setActiveState(stored);
      setLoading(false);
      return;
    }
    if (user.companies.length > 0) {
      const first = user.companies[0]!;
      await setActiveCompanyId(first.id);
      setActiveState(first.id);
      setActiveCompanyVersion((version) => version + 1);
    } else {
      setActiveState(null);
    }
    setLoading(false);
  }, [user]);

  useEffect(() => {
    if (authLoading) {
      return;
    }
    void reconcile();
  }, [authLoading, reconcile]);

  const setActive = useCallback(async (id: string) => {
    await setActiveCompanyId(id);
    setActiveState(id);
    setActiveCompanyVersion((version) => version + 1);
  }, []);

  const value = useMemo<CompanyState>(
    () => ({ activeCompanyId, activeCompanyVersion, loading, setActive, reconcile }),
    [activeCompanyId, activeCompanyVersion, loading, setActive, reconcile],
  );

  return <CompanyCtx.Provider value={value}>{children}</CompanyCtx.Provider>;
}

export function useActiveCompany(): CompanyState {
  const ctx = useContext(CompanyCtx);
  if (ctx === undefined) {
    throw new Error("useActiveCompany must be used inside a <CompanyProvider>");
  }
  return ctx;
}
