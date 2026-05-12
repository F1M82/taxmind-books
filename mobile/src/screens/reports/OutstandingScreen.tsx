import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  OutstandingResponse,
  OutstandingType,
  getOutstanding,
} from "../../api/reports";
import { formatINR } from "../../utils/money";

export default function OutstandingScreen(): React.ReactElement {
  const [type, setType] = useState<OutstandingType>("receivables");
  const [asOfDate, setAsOfDate] = useState<string>("");
  const [data, setData] = useState<OutstandingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await getOutstanding({
        type,
        as_of_date: asOfDate || undefined,
      });
      setData(resp);
    } catch {
      setError("Could not load outstanding balances.");
    } finally {
      setRefreshing(false);
    }
  }, [type, asOfDate]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
      <View style={styles.tabRow}>
        <TypeTab
          label="Receivables"
          active={type === "receivables"}
          onPress={() => setType("receivables")}
          a11y="pick-receivables"
        />
        <TypeTab
          label="Payables"
          active={type === "payables"}
          onPress={() => setType("payables")}
          a11y="pick-payables"
        />
      </View>

      <View style={styles.filterRow}>
        <Text style={styles.filterLabel}>As of</Text>
        <TextInput
          accessibilityLabel="as-of-date"
          value={asOfDate}
          onChangeText={setAsOfDate}
          placeholder="YYYY-MM-DD (today)"
          style={styles.filterInput}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="apply-filter"
          onPress={load}
          style={({ pressed }) => [
            styles.filterButton,
            pressed && { opacity: 0.85 },
          ]}
        >
          <Text style={styles.filterButtonText}>Apply</Text>
        </Pressable>
      </View>

      {data === null && error === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}

      <ScrollView
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={load} />
        }
      >
        {error !== null && <Text style={styles.error}>{error}</Text>}

        {data !== null && (
          <>
            <Text style={styles.subhead}>
              As of {data.as_of_date} ·{" "}
              {data.type === "receivables"
                ? "Sundry Debtors"
                : "Sundry Creditors"}
            </Text>

            {data.items.length === 0 && (
              <Text style={styles.empty}>No outstanding balances.</Text>
            )}

            {data.items.map((i) => (
              <View key={i.ledger_id} style={styles.row}>
                <View style={styles.rowMain}>
                  <Text style={styles.ledgerName}>{i.ledger_name}</Text>
                  {i.ledger_gstin !== null && (
                    <Text style={styles.gstin}>GSTIN {i.ledger_gstin}</Text>
                  )}
                </View>
                <View style={styles.rowAmount}>
                  <Text style={styles.amount}>{formatINR(i.balance)}</Text>
                  <Text style={styles.balanceType}>{i.balance_type}</Text>
                </View>
              </View>
            ))}

            <View style={styles.totalsRow}>
              <Text style={styles.totalsLabel}>Total</Text>
              <Text style={styles.totalsAmount}>
                {formatINR(data.total)} {data.total_type}
              </Text>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function TypeTab({
  label,
  active,
  onPress,
  a11y,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  a11y: string;
}): React.ReactElement {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y}
      onPress={onPress}
      style={({ pressed }) => [
        styles.tab,
        active && styles.tabActive,
        pressed && { opacity: 0.85 },
      ]}
    >
      <Text style={[styles.tabText, active && styles.tabTextActive]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
  tabRow: {
    flexDirection: "row",
    padding: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: "#bbb",
    borderRadius: 6,
    alignItems: "center",
  },
  tabActive: { backgroundColor: "#2c3e50", borderColor: "#2c3e50" },
  tabText: { fontSize: 14, color: "#333", fontWeight: "600" },
  tabTextActive: { color: "#fff" },
  filterRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
  },
  filterLabel: { fontSize: 14, color: "#666" },
  filterInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    fontSize: 14,
  },
  filterButton: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: "#2c3e50",
    borderRadius: 6,
  },
  filterButtonText: { color: "#fff", fontWeight: "600" },
  list: { padding: 16, gap: 8 },
  subhead: { fontSize: 12, color: "#666" },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 12,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
  },
  rowMain: { flex: 1, gap: 2 },
  rowAmount: { alignItems: "flex-end" },
  ledgerName: { fontSize: 15, fontWeight: "600" },
  gstin: { fontSize: 11, color: "#888" },
  amount: { fontSize: 15, fontWeight: "600" },
  balanceType: { fontSize: 11, color: "#666" },
  totalsRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    borderTopWidth: 2,
    borderTopColor: "#444",
    paddingTop: 10,
    marginTop: 6,
  },
  totalsLabel: { fontSize: 16, fontWeight: "700" },
  totalsAmount: { fontSize: 16, fontWeight: "700" },
  empty: { textAlign: "center", color: "#666", paddingVertical: 24 },
  error: { color: "#c0392b", padding: 16 },
});
