import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { AuditLogItem, listAuditLogs } from "../../api/audit";

// Human-friendly labels for the audit actions the admin log surfaces.
// Unknown actions fall back to the raw action string.
const ACTION_LABELS: Record<string, string> = {
  "voucher.created": "Voucher created",
  "voucher.posted_to_tally": "Voucher posted to Tally",
  "voucher.tally_post_failed": "Voucher Tally post failed",
  "voucher.tally_post_queued": "Voucher queued for Tally",
  "voucher.cancelled": "Voucher cancelled",
  "ledger.created": "Ledger created",
  "company.created": "Company created",
  "company.updated": "Company updated",
  "user_company.role_assigned": "Member added",
  "user_company.role_changed": "Member role changed",
  "user_company.removed": "Member removed",
  "connector.tally_companies_discovered": "Tally companies discovered",
};

function label(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

function when(iso: string): string {
  // Keep it dependency-free: trim the ISO string to "YYYY-MM-DD HH:MM".
  return iso.replace("T", " ").slice(0, 16);
}

export default function AuditLogScreen(): React.ReactElement {
  const [items, setItems] = useState<AuditLogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const resp = await listAuditLogs({ limit: 100 });
      setItems(resp.items);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Could not load the activity log.",
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <View style={styles.container}>
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
        {items !== null && items.length === 0 && error === null && (
          <Text style={styles.empty}>No activity yet.</Text>
        )}
        {items !== null &&
          items.map((it) => (
            <View key={it.id} style={styles.row}>
              <Text style={styles.action}>{label(it.action)}</Text>
              <Text style={styles.meta}>
                {it.user_email ?? "system"} · {when(it.created_at)}
              </Text>
            </View>
          ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
  list: { paddingHorizontal: 16, paddingVertical: 16, gap: 8 },
  row: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 4,
  },
  action: { fontSize: 15, fontWeight: "600" },
  meta: { fontSize: 13, color: "#666" },
  empty: { textAlign: "center", color: "#666", padding: 24 },
  error: { color: "#c0392b", padding: 16 },
});
