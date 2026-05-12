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
  TrialBalanceResponse,
  getTrialBalance,
} from "../../api/reports";
import { formatINR } from "../../utils/money";

export default function TrialBalanceScreen(): React.ReactElement {
  const [asOfDate, setAsOfDate] = useState<string>("");
  const [data, setData] = useState<TrialBalanceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await getTrialBalance(
        asOfDate ? { as_of_date: asOfDate } : {},
      );
      setData(resp);
    } catch {
      setError("Could not load trial balance.");
    } finally {
      setRefreshing(false);
    }
  }, [asOfDate]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
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
            <Text style={styles.subhead}>As of {data.as_of_date}</Text>

            <View style={styles.headerRow}>
              <Text style={[styles.colLedger, styles.headerCell]}>Ledger</Text>
              <Text style={[styles.colAmount, styles.headerCell]}>Dr</Text>
              <Text style={[styles.colAmount, styles.headerCell]}>Cr</Text>
            </View>

            {data.ledgers.length === 0 && (
              <Text style={styles.empty}>No ledger activity.</Text>
            )}

            {data.ledgers.map((l) => (
              <View key={l.ledger_id} style={styles.row}>
                <View style={styles.colLedger}>
                  <Text style={styles.ledgerName}>{l.ledger_name}</Text>
                  {l.group_name !== null && (
                    <Text style={styles.groupName}>{l.group_name}</Text>
                  )}
                </View>
                <Text style={styles.colAmount}>
                  {l.closing_balance_type === "Dr"
                    ? formatINR(l.closing_balance)
                    : "—"}
                </Text>
                <Text style={styles.colAmount}>
                  {l.closing_balance_type === "Cr"
                    ? formatINR(l.closing_balance)
                    : "—"}
                </Text>
              </View>
            ))}

            <View style={[styles.row, styles.totalsRow]}>
              <Text style={[styles.colLedger, styles.totalsLabel]}>Total</Text>
              <Text style={[styles.colAmount, styles.totalsAmount]}>
                {formatINR(data.totals.total_dr)}
              </Text>
              <Text style={[styles.colAmount, styles.totalsAmount]}>
                {formatINR(data.totals.total_cr)}
              </Text>
            </View>

            <Text
              accessibilityLabel="in-balance"
              style={[
                styles.balanceFlag,
                data.totals.in_balance
                  ? styles.balanceOk
                  : styles.balanceBad,
              ]}
            >
              {data.totals.in_balance ? "In balance" : "Out of balance"}
            </Text>

            {(data.exclusions.optional_vouchers_excluded_count > 0 ||
              data.exclusions.cancelled_vouchers_excluded_count > 0) && (
              <Text style={styles.exclusions}>
                Excluded: {data.exclusions.optional_vouchers_excluded_count}{" "}
                Optional, {data.exclusions.cancelled_vouchers_excluded_count}{" "}
                Cancelled
              </Text>
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
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
  list: { padding: 16, gap: 4 },
  subhead: { fontSize: 12, color: "#666", marginBottom: 6 },
  headerRow: {
    flexDirection: "row",
    paddingVertical: 6,
    borderBottomWidth: 1,
    borderBottomColor: "#ddd",
  },
  headerCell: { fontWeight: "600", color: "#444", fontSize: 13 },
  row: {
    flexDirection: "row",
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#f0f0f0",
    alignItems: "center",
  },
  colLedger: { flex: 2 },
  colAmount: { flex: 1, textAlign: "right", fontSize: 14 },
  ledgerName: { fontSize: 14, color: "#222" },
  groupName: { fontSize: 11, color: "#888" },
  totalsRow: {
    borderTopWidth: 2,
    borderTopColor: "#444",
    borderBottomWidth: 0,
    paddingTop: 8,
  },
  totalsLabel: { fontWeight: "700" },
  totalsAmount: { fontWeight: "700" },
  balanceFlag: {
    alignSelf: "flex-start",
    marginTop: 12,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    fontSize: 12,
    overflow: "hidden",
  },
  balanceOk: { backgroundColor: "#27ae60", color: "#fff" },
  balanceBad: { backgroundColor: "#c0392b", color: "#fff" },
  exclusions: { fontSize: 12, color: "#666", marginTop: 8 },
  empty: { textAlign: "center", color: "#666", paddingVertical: 24 },
  error: { color: "#c0392b", padding: 16 },
});
