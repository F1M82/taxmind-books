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

import { LedgerListItem, listLedgers } from "../../api/ledgers";
import { formatINR } from "../../utils/money";

export default function LedgerListScreen({
  onPickLedger,
  onCreate,
}: {
  /** Optional — used by the voucher entry flow as a ledger picker. */
  onPickLedger?: (ledger: LedgerListItem) => void;
  /** Optional — omitted when this screen is embedded as a picker. */
  onCreate?: () => void;
}): React.ReactElement {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<LedgerListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(
    async (q?: string) => {
      setError(null);
      setRefreshing(true);
      try {
        const resp = await listLedgers(
          q !== undefined && q.length > 0 ? { q } : undefined,
        );
        setItems(resp.items);
      } catch {
        setError("Could not load ledgers.");
      } finally {
        setRefreshing(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
      {onCreate !== undefined && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="new-ledger"
          onPress={onCreate}
          style={({ pressed }) => [
            styles.newButton,
            pressed && { opacity: 0.85 },
          ]}
        >
          <Text style={styles.newButtonText}>+ New ledger</Text>
        </Pressable>
      )}
      <TextInput
        accessibilityLabel="ledger-search"
        placeholder="Search ledgers (fuzzy)"
        value={query}
        onChangeText={(v) => {
          setQuery(v);
          void load(v.trim());
        }}
        style={styles.search}
      />
      {items === null && error === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}
      <ScrollView
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load(query.trim())}
          />
        }
      >
        {error !== null && <Text style={styles.error}>{error}</Text>}
        {items !== null && items.length === 0 && (
          <Text style={styles.empty}>No ledgers found.</Text>
        )}
        {items !== null &&
          items.map((led) => (
            <Pressable
              key={led.id}
              accessibilityRole="button"
              accessibilityLabel={`pick-${led.id}`}
              onPress={() => onPickLedger?.(led)}
              style={({ pressed }) => [
                styles.row,
                pressed && { opacity: 0.85 },
              ]}
            >
              <View style={styles.rowMain}>
                <Text style={styles.name}>{led.name}</Text>
                {led.group_name !== null && (
                  <Text style={styles.meta}>{led.group_name}</Text>
                )}
                {led.created_via_mobile && led.confirmed_in_tally_at === null && (
                  <Text style={styles.pendingTag}>Pending Tally sync</Text>
                )}
              </View>
              <View style={styles.rowRight}>
                <Text style={styles.amount}>
                  {formatINR(led.opening_balance)}
                </Text>
                <Text style={styles.meta}>{led.balance_type}</Text>
              </View>
            </Pressable>
          ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { flex: 1, justifyContent: "center", alignItems: "center" },
  newButton: {
    margin: 16,
    marginBottom: 0,
    padding: 14,
    borderRadius: 8,
    backgroundColor: "#2c3e50",
    alignItems: "center",
  },
  newButtonText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  pendingTag: { fontSize: 12, color: "#d35400", fontWeight: "600" },
  search: {
    margin: 16,
    padding: 12,
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    fontSize: 16,
  },
  list: { paddingHorizontal: 16, paddingBottom: 24, gap: 8 },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
  },
  rowMain: { gap: 2, flexShrink: 1 },
  rowRight: { alignItems: "flex-end", gap: 2 },
  name: { fontSize: 16, fontWeight: "600" },
  amount: { fontSize: 14 },
  meta: { fontSize: 13, color: "#666" },
  empty: { textAlign: "center", color: "#666", padding: 24 },
  error: { color: "#c0392b", padding: 16 },
});
