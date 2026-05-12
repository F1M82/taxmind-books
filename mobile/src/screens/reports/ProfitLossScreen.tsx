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

import { ProfitLossResponse, getProfitLoss } from "../../api/reports";
import { formatINR } from "../../utils/money";

export default function ProfitLossScreen(): React.ReactElement {
  const [fromDate, setFromDate] = useState<string>("");
  const [toDate, setToDate] = useState<string>("");
  const [data, setData] = useState<ProfitLossResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await getProfitLoss({
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      });
      setData(resp);
    } catch {
      setError("Could not load profit & loss.");
    } finally {
      setRefreshing(false);
    }
  }, [fromDate, toDate]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
      <View style={styles.filterBlock}>
        <View style={styles.filterRow}>
          <Text style={styles.filterLabel}>From</Text>
          <TextInput
            accessibilityLabel="from-date"
            value={fromDate}
            onChangeText={setFromDate}
            placeholder="YYYY-MM-DD (FY start)"
            style={styles.filterInput}
            autoCapitalize="none"
            autoCorrect={false}
          />
        </View>
        <View style={styles.filterRow}>
          <Text style={styles.filterLabel}>To</Text>
          <TextInput
            accessibilityLabel="to-date"
            value={toDate}
            onChangeText={setToDate}
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
              {data.from_date} → {data.to_date}
            </Text>

            <Section title="Income" total={data.income.total}>
              {data.income.ledgers.map((l) => (
                <LineRow
                  key={l.ledger_id}
                  name={l.ledger_name}
                  amount={l.amount}
                />
              ))}
            </Section>

            <Section title="Expense" total={data.expense.total}>
              {data.expense.ledgers.map((l) => (
                <LineRow
                  key={l.ledger_id}
                  name={l.ledger_name}
                  amount={l.amount}
                />
              ))}
            </Section>

            <View
              accessibilityLabel="net-row"
              style={[
                styles.netRow,
                data.net.type === "profit" ? styles.netProfit : styles.netLoss,
              ]}
            >
              <Text style={styles.netLabel}>
                Net {data.net.type === "profit" ? "Profit" : "Loss"}
              </Text>
              <Text style={styles.netAmount}>{formatINR(data.net.value)}</Text>
            </View>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function Section({
  title,
  total,
  children,
}: {
  title: string;
  total: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {React.Children.count(children) === 0 ? (
        <Text style={styles.empty}>No {title.toLowerCase()} entries.</Text>
      ) : (
        children
      )}
      <View style={styles.sectionTotalRow}>
        <Text style={styles.sectionTotalLabel}>Total {title}</Text>
        <Text style={styles.sectionTotalAmount}>{formatINR(total)}</Text>
      </View>
    </View>
  );
}

function LineRow({
  name,
  amount,
}: {
  name: string;
  amount: string;
}): React.ReactElement {
  return (
    <View style={styles.lineRow}>
      <Text style={styles.lineName}>{name}</Text>
      <Text style={styles.lineAmount}>{formatINR(amount)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
  filterBlock: {
    padding: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
  },
  filterRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  filterLabel: { fontSize: 14, color: "#666", width: 44 },
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
    gap: 4,
  },
  sectionTitle: { fontSize: 16, fontWeight: "600" },
  lineRow: { flexDirection: "row", paddingVertical: 4 },
  lineName: { flex: 2, fontSize: 14, color: "#222" },
  lineAmount: { flex: 1, fontSize: 14, textAlign: "right" },
  sectionTotalRow: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: "#ccc",
    paddingTop: 6,
    marginTop: 4,
  },
  sectionTotalLabel: { flex: 2, fontWeight: "600" },
  sectionTotalAmount: { flex: 1, fontWeight: "600", textAlign: "right" },
  netRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 14,
    borderRadius: 8,
  },
  netProfit: { backgroundColor: "#27ae60" },
  netLoss: { backgroundColor: "#c0392b" },
  netLabel: { color: "#fff", fontWeight: "700", fontSize: 16 },
  netAmount: { color: "#fff", fontWeight: "700", fontSize: 16 },
  empty: { color: "#666", fontStyle: "italic", paddingVertical: 4 },
  error: { color: "#c0392b", padding: 16 },
});
