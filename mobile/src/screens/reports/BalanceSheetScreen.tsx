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
  BSGroup,
  BSSection,
  BalanceSheetResponse,
  getBalanceSheet,
} from "../../api/reports";
import { formatINR } from "../../utils/money";

export default function BalanceSheetScreen(): React.ReactElement {
  const [asOfDate, setAsOfDate] = useState<string>("");
  const [data, setData] = useState<BalanceSheetResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await getBalanceSheet(
        asOfDate ? { as_of_date: asOfDate } : {},
      );
      setData(resp);
    } catch {
      setError(
        "Could not load balance sheet. " +
          "If this persists, the books may be out of balance — contact support.",
      );
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

            <BSSectionView title="Assets" section={data.assets} />

            <BSSectionView title="Liabilities" section={data.liabilities} />

            <View style={styles.pnlRow}>
              <Text style={styles.pnlLabel}>
                Current period{" "}
                {data.current_period_profit_loss.type === "profit"
                  ? "profit"
                  : "loss"}
              </Text>
              <Text style={styles.pnlAmount}>
                {formatINR(data.current_period_profit_loss.value)}
              </Text>
            </View>

            <View
              accessibilityLabel="equation"
              style={[
                styles.equation,
                data.equation.in_balance
                  ? styles.equationOk
                  : styles.equationBad,
              ]}
            >
              <Text style={styles.equationText}>
                Assets {formatINR(data.equation.assets)} ={" "}
                {formatINR(data.equation.liabilities_plus_equity)}{" "}
                Liabilities + Equity
              </Text>
              <Text style={styles.equationFlag}>
                {data.equation.in_balance ? "In balance" : "Out of balance"}
              </Text>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function BSSectionView({
  title,
  section,
}: {
  title: string;
  section: BSSection;
}): React.ReactElement {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {section.groups.length === 0 && (
        <Text style={styles.empty}>No {title.toLowerCase()}.</Text>
      )}
      {section.groups.map((g) => (
        <GroupBlock key={g.group_name} group={g} />
      ))}
      <View style={styles.sectionTotalRow}>
        <Text style={styles.sectionTotalLabel}>Total {title}</Text>
        <Text style={styles.sectionTotalAmount}>
          {formatINR(section.total)}
        </Text>
      </View>
    </View>
  );
}

function GroupBlock({ group }: { group: BSGroup }): React.ReactElement {
  return (
    <View style={styles.group}>
      <Text style={styles.groupName}>{group.group_name}</Text>
      {group.ledgers.map((l) => (
        <View key={l.ledger_id} style={styles.lineRow}>
          <Text style={styles.lineName}>{l.ledger_name}</Text>
          <Text style={styles.lineAmount}>{formatINR(l.amount)}</Text>
        </View>
      ))}
      <View style={styles.groupTotalRow}>
        <Text style={styles.groupTotalLabel}>Group total</Text>
        <Text style={styles.groupTotalAmount}>{formatINR(group.total)}</Text>
      </View>
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
  list: { padding: 16, gap: 12 },
  subhead: { fontSize: 12, color: "#666" },
  section: {
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    padding: 12,
    gap: 8,
  },
  sectionTitle: { fontSize: 16, fontWeight: "700" },
  group: {
    borderLeftWidth: 3,
    borderLeftColor: "#bbb",
    paddingLeft: 10,
    gap: 2,
  },
  groupName: { fontSize: 14, fontWeight: "600", color: "#333" },
  lineRow: { flexDirection: "row", paddingVertical: 2 },
  lineName: { flex: 2, fontSize: 13, color: "#444" },
  lineAmount: { flex: 1, fontSize: 13, textAlign: "right" },
  groupTotalRow: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: "#eee",
    paddingTop: 4,
    marginTop: 2,
  },
  groupTotalLabel: {
    flex: 2,
    fontSize: 12,
    fontWeight: "600",
    color: "#666",
  },
  groupTotalAmount: {
    flex: 1,
    fontSize: 12,
    fontWeight: "600",
    textAlign: "right",
    color: "#666",
  },
  sectionTotalRow: {
    flexDirection: "row",
    borderTopWidth: 2,
    borderTopColor: "#444",
    paddingTop: 6,
  },
  sectionTotalLabel: { flex: 2, fontWeight: "700" },
  sectionTotalAmount: { flex: 1, fontWeight: "700", textAlign: "right" },
  pnlRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 10,
    backgroundColor: "#f3f3f3",
    borderRadius: 6,
  },
  pnlLabel: { fontSize: 14, fontWeight: "600" },
  pnlAmount: { fontSize: 14, fontWeight: "600" },
  equation: { padding: 12, borderRadius: 8, gap: 4 },
  equationOk: { backgroundColor: "#27ae60" },
  equationBad: { backgroundColor: "#c0392b" },
  equationText: { color: "#fff", fontSize: 13 },
  equationFlag: { color: "#fff", fontSize: 13, fontWeight: "700" },
  empty: { color: "#666", fontStyle: "italic" },
  error: { color: "#c0392b", padding: 16 },
});
