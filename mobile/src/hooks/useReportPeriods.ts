import { useEffect, useState } from "react";

import { FinancialYear, getReportPeriods } from "../api/reports";

/**
 * Financial years offered by the report period picker (newest first).
 *
 * The picker is an enhancement over the manual date fields, so a failed
 * fetch must never break a report screen: on any error this returns `[]`
 * and the picker simply doesn't render.
 */
export function useReportPeriods(): FinancialYear[] {
  const [periods, setPeriods] = useState<FinancialYear[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const resp = await getReportPeriods();
        if (!cancelled && Array.isArray(resp?.items)) {
          setPeriods(resp.items);
        }
      } catch {
        // Leave the list empty; manual date entry still works.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return periods;
}
