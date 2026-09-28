/**
 * Owner/admin: record each financial year's opening/closing STOCK value from
 * Tally. TaxMind has no inventory, so profit & loss and the balance sheet use
 * these figures for stock (docs/PHASE_3_CLOSING_STOCK_DESIGN.md).
 *
 * The operator sets the period at Tally's Gateway of Tally main menu (F2) to
 * ONE financial year, then taps "Read stock from Tally". Tally's own figures
 * are mirrored as-is -- including a net credit when Tally has negative stock.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import {
  StockValuation,
  listStockValuations,
  pullStockValuation,
} from "../../api/stockValuation";
import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";
import { formatINR } from "../../utils/money";

export default function StockValuationScreen(): React.ReactElement {
  const { user } = useAuth();
  const { activeCompanyId } = useActiveCompany();
  const [items, setItems] = useState<StockValuation[] | null>(null);
  const [pulling, setPulling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<StockValuation | null>(null);

  const activeCompany =
    activeCompanyId === null
      ? null
      : (user?.companies.find((c) => c.id === activeCompanyId) ?? null);
  const canPull =
    activeCompany?.role === "owner" || activeCompany?.role === "admin";

  const load = useCallback(async () => {
    try {
      const resp = await listStockValuations();
      setItems(resp.items);
    } catch {
      setError("Could not load stock valuations.");
      setItems([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onPull = useCallback(async () => {
    if (activeCompanyId === null) return;
    setError(null);
    setNotice(null);
    setPulling(true);
    try {
      const v = await pullStockValuation(activeCompanyId);
      setNotice(v);
      await load();
    } catch (e) {
      // 422 stock_period_not_single_fy / 409 stock_opening_mismatch etc.
      // carry an operator-facing message; show it verbatim.
      setError(
        e instanceof ApiError ? e.message : "Could not read stock from Tally.",
      );
    } finally {
      setPulling(false);
    }
  }, [activeCompanyId, load]);

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.help}>
        In Tally, at the Gateway of Tally main menu press F2 and set the period
        to one financial year (for example 1-Apr-25 to 31-Mar-26). Then tap the
        button below. Repeat for each year.
      </Text>

      {canPull ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="pull-stock"
          onPress={onPull}
          disabled={pulling}
          style={({ pressed }) => [
            styles.button,
            (pressed || pulling) && { opacity: 0.7 },
          ]}
        >
          {pulling ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Read stock from Tally</Text>
          )}
        </Pressable>
      ) : (
        <Text style={styles.muted}>
          Only an owner or admin can read stock from Tally.
        </Text>
      )}

      {error !== null && (
        <Text accessibilityLabel="pull-error" style={styles.error}>
          {error}
        </Text>
      )}
      {notice !== null && (
        <Text accessibilityLabel="pull-notice" style={styles.notice}>
          Recorded {notice.label}.
        </Text>
      )}

      {items === null && <ActivityIndicator />}
      {items !== null && items.length === 0 && (
        <Text style={styles.muted}>No stock recorded yet.</Text>
      )}
      {items?.map((v) => (
        <View key={v.id} style={styles.card}>
          <Text style={styles.cardTitle}>{v.label}</Text>
          <Row label="Opening stock" amount={v.opening_value} />
          <Row label="Closing stock" amount={v.closing_value} />
          <Text style={styles.meta}>
            {v.source === "tally" ? "From Tally" : "Entered manually"} ·{" "}
            {v.captured_at.slice(0, 10)}
          </Text>
          {(v.negative_stock_items ?? 0) > 0 && (
            <Text accessibilityLabel={`negative-${v.label}`} style={styles.warn}>
              {v.negative_stock_items} item(s) have negative stock in Tally
              (more issued than received). Please review them in Tally.
            </Text>
          )}
        </View>
      ))}
    </ScrollView>
  );
}

function Row({
  label,
  amount,
}: {
  label: string;
  amount: string;
}): React.ReactElement {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowAmount}>{formatINR(amount)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  help: { fontSize: 13, color: "#555" },
  muted: { color: "#666", fontStyle: "italic" },
  button: {
    backgroundColor: "#2c3e50",
    paddingVertical: 12,
    borderRadius: 8,
    alignItems: "center",
  },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  error: { color: "#c0392b" },
  notice: { color: "#27ae60", fontWeight: "600" },
  card: {
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    padding: 12,
    gap: 4,
  },
  cardTitle: { fontSize: 16, fontWeight: "600" },
  row: { flexDirection: "row", justifyContent: "space-between" },
  rowLabel: { fontSize: 14, color: "#222" },
  rowAmount: { fontSize: 14 },
  meta: { fontSize: 12, color: "#666", marginTop: 4 },
  warn: { fontSize: 12, color: "#b9770e", marginTop: 4 },
});
