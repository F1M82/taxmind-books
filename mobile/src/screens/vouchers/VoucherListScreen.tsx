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

import { VoucherListItem, listVouchers } from "../../api/vouchers";
import { formatINR } from "../../utils/money";

// P0.46d: status is "live in the books" (posted or pending_tally_post);
// tally_posted_at is the orthogonal "mirrored to Tally" signal. The
// `pending_tally_post` state surfaces as "Queued for Tally" so the user
// knows their entry is real even when Tally is offline / on the wrong
// company / not yet acknowledged. A pending voucher carrying
// `tally_last_error` after at least one attempt is a non-retryable
// Tally rejection (validation error, missing ledger) — surface it
// differently so the user knows a human needs to act.
function isTallyRejected(v: VoucherListItem): boolean {
  return (
    v.status === "pending_tally_post" &&
    v.tally_last_error !== null &&
    (v.tally_post_queued_at === null ||
      // attempts > 0 is inferred from the presence of an error message;
      // VoucherListItem doesn't currently carry tally_post_attempts so
      // we treat any error on a pending row as a rejection signal.
      true)
  );
}

function tallyTagLabel(v: VoucherListItem): string {
  if (v.status === "cancelled") return "Cancelled";
  if (v.status === "rejected_optional") return "Rejected";
  if (v.tally_posted_at !== null) return "Posted to Tally";
  if (isTallyRejected(v)) return "Tally rejected";
  if (v.status === "pending_tally_post") return "Queued for Tally";
  return "Pending Tally";
}

function tallyTagStyle(v: VoucherListItem) {
  if (v.status === "cancelled" || v.status === "rejected_optional") {
    return styles.tallyCancelled;
  }
  if (v.tally_posted_at !== null) return styles.tallyPosted;
  if (isTallyRejected(v)) return styles.tallyRejected;
  if (v.status === "pending_tally_post") return styles.tallyQueued;
  return styles.tallyPending;
}

export default function VoucherListScreen({
  onCreate,
}: {
  onCreate: () => void;
}): React.ReactElement {
  const [items, setItems] = useState<VoucherListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await listVouchers();
      setItems(resp.items);
    } catch {
      setError("Could not load vouchers.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="new-voucher"
        onPress={onCreate}
        style={({ pressed }) => [
          styles.newButton,
          pressed && { opacity: 0.85 },
        ]}
      >
        <Text style={styles.newButtonText}>+ New voucher</Text>
      </Pressable>
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
          <Text style={styles.empty}>No vouchers yet.</Text>
        )}
        {items !== null &&
          items.map((v) => (
            <View key={v.id} style={styles.row}>
              <View style={styles.rowMain}>
                <Text style={styles.title}>
                  {v.voucher_type}
                  {v.voucher_number ? ` · ${v.voucher_number}` : ""}
                </Text>
                <Text style={styles.meta}>
                  {v.date}
                  {v.narration ? ` · ${v.narration}` : ""}
                </Text>
                <Text
                  style={[styles.tallyTag, tallyTagStyle(v)]}
                >
                  {tallyTagLabel(v)}
                </Text>
                {v.tally_last_error !== null && (
                  <Text style={styles.errorHint}>
                    Last error: {v.tally_last_error}
                  </Text>
                )}
              </View>
              <Text style={styles.amount}>{formatINR(v.total_amount)}</Text>
            </View>
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
    padding: 14,
    borderRadius: 8,
    backgroundColor: "#2c3e50",
    alignItems: "center",
  },
  newButtonText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  list: { paddingHorizontal: 16, paddingBottom: 24, gap: 8 },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
  },
  rowMain: { gap: 4, flexShrink: 1 },
  title: { fontSize: 16, fontWeight: "600" },
  meta: { fontSize: 13, color: "#666" },
  amount: { fontSize: 16, fontWeight: "600" },
  tallyTag: {
    fontSize: 12,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    alignSelf: "flex-start",
    overflow: "hidden",
  },
  tallyPosted: { backgroundColor: "#27ae60", color: "#fff" },
  tallyPending: { backgroundColor: "#f39c12", color: "#fff" },
  tallyQueued: { backgroundColor: "#3498db", color: "#fff" },
  tallyRejected: { backgroundColor: "#c0392b", color: "#fff" },
  tallyCancelled: { backgroundColor: "#95a5a6", color: "#fff" },
  errorHint: { fontSize: 12, color: "#c0392b" },
  empty: { textAlign: "center", color: "#666", padding: 24 },
  error: { color: "#c0392b", padding: 16 },
});
