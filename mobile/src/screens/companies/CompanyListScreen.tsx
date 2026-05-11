import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { CompanyListItem, listCompanies } from "../../api/companies";
import { useActiveCompany } from "../../context/CompanyContext";

export default function CompanyListScreen({
  onCreate,
  onPick,
}: {
  onCreate: () => void;
  onPick: () => void;
}): React.ReactElement {
  const { activeCompanyId, setActive } = useActiveCompany();
  const [items, setItems] = useState<CompanyListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await listCompanies();
      setItems(resp.items);
    } catch {
      setError("Could not load your companies.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (items === null && error === null) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} />
      }
    >
      <Text style={styles.title}>Your companies</Text>
      {error !== null && <Text style={styles.error}>{error}</Text>}
      {items !== null && items.length === 0 && (
        <Text>You don't belong to any company yet.</Text>
      )}
      {items !== null &&
        items.map((c) => {
          const isActive = c.id === activeCompanyId;
          return (
            <Pressable
              key={c.id}
              accessibilityRole="button"
              accessibilityLabel={`select-${c.id}`}
              onPress={async () => {
                await setActive(c.id);
                onPick();
              }}
              style={({ pressed }) => [
                styles.card,
                isActive && styles.cardActive,
                pressed && styles.cardPressed,
              ]}
            >
              <View style={styles.cardHeader}>
                <Text style={styles.companyName}>{c.name}</Text>
                {isActive && <Text style={styles.activeBadge}>active</Text>}
              </View>
              <Text style={styles.companyMeta}>
                {c.gstin ?? "no GSTIN"} · {c.your_role}
              </Text>
            </Pressable>
          );
        })}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="create-company"
        onPress={onCreate}
        style={({ pressed }) => [
          styles.createButton,
          pressed && { opacity: 0.85 },
        ]}
      >
        <Text style={styles.createButtonText}>+ Create a new company</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, gap: 12 },
  center: { flex: 1, justifyContent: "center", alignItems: "center" },
  title: { fontSize: 24, fontWeight: "600" },
  error: { color: "#c0392b" },
  card: {
    padding: 16,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 4,
  },
  cardActive: { borderColor: "#2c3e50", backgroundColor: "#f7f9fb" },
  cardPressed: { opacity: 0.9 },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  companyName: { fontSize: 16, fontWeight: "600" },
  activeBadge: {
    backgroundColor: "#2c3e50",
    color: "#fff",
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
    fontSize: 12,
  },
  companyMeta: { fontSize: 14, color: "#666" },
  createButton: {
    marginTop: 12,
    padding: 14,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#2c3e50",
    alignItems: "center",
  },
  createButtonText: { color: "#2c3e50", fontWeight: "600" },
});
