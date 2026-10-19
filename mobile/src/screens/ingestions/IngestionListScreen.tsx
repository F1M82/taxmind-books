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

import { IngestionListItem, listIngestions } from "../../api/ingestions";

function statusLabel(status: string): string {
  switch (status) {
    case "review":
      return "Needs review";
    case "matched":
      return "Matched — needs voucher";
    case "posted":
      return "Posted";
    case "discarded":
      return "Discarded";
    case "failed":
      return "Extraction failed";
    default:
      return "Processing…";
  }
}

function statusStyle(status: string) {
  switch (status) {
    case "review":
      return styles.tagReview;
    case "matched":
      return styles.tagMatched;
    case "posted":
      return styles.tagPosted;
    case "discarded":
      return styles.tagDiscarded;
    case "failed":
      return styles.tagFailed;
    default:
      return styles.tagProcessing;
  }
}

export default function IngestionListScreen({
  onOpenDetail,
}: {
  onOpenDetail: (ingestionId: string) => void;
}): React.ReactElement {
  const [items, setItems] = useState<IngestionListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await listIngestions();
      setItems(resp.items);
    } catch {
      setError("Could not load scanned invoices.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
      <Text style={styles.intro}>
        Invoices scanned from the mobile app. Items marked "Needs review" or
        "Matched" still need a voucher created by hand.
      </Text>
      {items === null && error === null && (
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
        {items !== null && items.length === 0 && (
          <Text style={styles.empty}>No scanned invoices yet.</Text>
        )}
        {items !== null &&
          items.map((ing) => (
            <Pressable
              key={ing.id}
              accessibilityRole="button"
              accessibilityLabel={`open-ingestion-${ing.id}`}
              onPress={() => onOpenDetail(ing.id)}
              style={({ pressed }) => [
                styles.row,
                pressed && { opacity: 0.85 },
              ]}
            >
              <View style={styles.rowMain}>
                <Text style={styles.meta}>
                  {new Date(ing.received_at).toLocaleString()}
                </Text>
                <Text style={[styles.tag, statusStyle(ing.status)]}>
                  {statusLabel(ing.status)}
                </Text>
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
  intro: {
    margin: 16,
    marginBottom: 0,
    fontSize: 13,
    color: "#444",
    lineHeight: 18,
  },
  list: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 24, gap: 8 },
  row: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
  },
  rowMain: { gap: 6 },
  meta: { fontSize: 13, color: "#666" },
  tag: {
    fontSize: 12,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    alignSelf: "flex-start",
    overflow: "hidden",
  },
  tagProcessing: { backgroundColor: "#3498db", color: "#fff" },
  tagReview: { backgroundColor: "#c0392b", color: "#fff" },
  tagMatched: { backgroundColor: "#f39c12", color: "#fff" },
  tagPosted: { backgroundColor: "#27ae60", color: "#fff" },
  tagDiscarded: { backgroundColor: "#95a5a6", color: "#fff" },
  tagFailed: { backgroundColor: "#a04000", color: "#fff" },
  empty: { textAlign: "center", color: "#666", padding: 24 },
  error: { color: "#c0392b", padding: 16 },
});
