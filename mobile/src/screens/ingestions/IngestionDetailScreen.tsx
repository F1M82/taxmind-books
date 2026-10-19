import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import {
  IngestionOut,
  discardIngestion,
  getIngestion,
} from "../../api/ingestions";

const RESOLVABLE = new Set(["review", "matched", "failed"]);

function fieldRows(ing: IngestionOut): { label: string; value: string }[] {
  const data = ing.parsed_data ?? {};
  const rows: { label: string; value: string }[] = [];
  const push = (label: string, key: string) => {
    const v = data[key];
    if (v !== undefined && v !== null && v !== "") {
      rows.push({ label, value: String(v) });
    }
  };
  push("Vendor", "vendor_name");
  push("Vendor GSTIN", "vendor_gstin");
  push("Invoice number", "invoice_number");
  push("Invoice date", "invoice_date");
  push("Total amount", "total_amount");
  push("CGST", "cgst");
  push("SGST", "sgst");
  push("IGST", "igst");
  push("Suggested type", "suggested_voucher_type");
  return rows;
}

export default function IngestionDetailScreen({
  ingestionId,
  onCreateVoucher,
  onResolved,
  onBack,
}: {
  ingestionId: string;
  /** Navigate to voucher-create, carrying this ingestion's id to auto-link on success. */
  onCreateVoucher: (ingestionId: string) => void;
  onResolved: () => void;
  onBack: () => void;
}): React.ReactElement {
  const [ingestion, setIngestion] = useState<IngestionOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setIngestion(await getIngestion(ingestionId));
    } catch {
      setError("Could not load this item.");
    }
  }, [ingestionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onDiscard = useCallback(() => {
    Alert.alert(
      "Discard this item",
      "Use this when no voucher is needed (duplicate scan, handled elsewhere).",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            void (async () => {
              setBusy(true);
              try {
                await discardIngestion(ingestionId);
                onResolved();
              } catch (e) {
                Alert.alert(
                  "Could not discard",
                  e instanceof ApiError ? e.message : "Unknown error",
                );
              } finally {
                setBusy(false);
              }
            })();
          },
        },
      ],
    );
  }, [ingestionId, onResolved]);

  if (error !== null) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
      </View>
    );
  }
  if (ingestion === null) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  const resolvable = RESOLVABLE.has(ingestion.status);
  const rows = fieldRows(ingestion);

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Status: {ingestion.status}</Text>
      {ingestion.extraction_confidence !== null && (
        <Text style={styles.meta}>
          Confidence: {Math.round(Number(ingestion.extraction_confidence) * 100)}%
        </Text>
      )}
      {ingestion.extraction_flags.length > 0 && (
        <Text style={styles.flags}>
          Flags: {ingestion.extraction_flags.join(", ")}
        </Text>
      )}
      {ingestion.failure_reason !== null && (
        <Text style={styles.error}>{ingestion.failure_reason}</Text>
      )}

      <View style={styles.card}>
        {rows.length === 0 && (
          <Text style={styles.meta}>No extracted data to show.</Text>
        )}
        {rows.map((r) => (
          <View key={r.label} style={styles.fieldRow}>
            <Text style={styles.fieldLabel}>{r.label}</Text>
            <Text style={styles.fieldValue}>{r.value}</Text>
          </View>
        ))}
      </View>

      {ingestion.posted_voucher_id !== null && (
        <Text style={styles.meta}>
          Linked voucher: {ingestion.posted_voucher_id}
        </Text>
      )}

      {resolvable && (
        <Text style={styles.hint}>
          Create the voucher by hand using the fields above, then come back
          here only if it doesn't auto-link — or discard this item if no
          voucher is needed.
        </Text>
      )}

      {resolvable && (
        <View style={styles.actions}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="create-voucher-from-ingestion"
            disabled={busy}
            onPress={() => onCreateVoucher(ingestionId)}
            style={({ pressed }) => [
              styles.btn,
              pressed && { opacity: 0.85 },
            ]}
          >
            <Text style={styles.btnText}>Create voucher</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="discard-ingestion"
            disabled={busy}
            onPress={onDiscard}
            style={({ pressed }) => [
              styles.btnSecondary,
              pressed && { opacity: 0.85 },
            ]}
          >
            {busy ? (
              <ActivityIndicator />
            ) : (
              <Text style={styles.btnSecondaryText}>Discard</Text>
            )}
          </Pressable>
        </View>
      )}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="back-to-queue"
        onPress={onBack}
        style={({ pressed }) => [styles.btnLink, pressed && { opacity: 0.85 }]}
      >
        <Text style={styles.btnLinkText}>Back to review queue</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  center: { flex: 1, justifyContent: "center", alignItems: "center" },
  title: { fontSize: 18, fontWeight: "600" },
  meta: { fontSize: 13, color: "#666" },
  flags: { fontSize: 13, color: "#d35400" },
  error: { color: "#c0392b" },
  card: {
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    padding: 14,
    gap: 8,
  },
  fieldRow: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  fieldLabel: { color: "#666", fontSize: 13 },
  fieldValue: { fontSize: 13, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  hint: { fontSize: 13, color: "#444", lineHeight: 18 },
  actions: { gap: 10 },
  btn: {
    padding: 14,
    borderRadius: 8,
    backgroundColor: "#2c3e50",
    alignItems: "center",
  },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  btnSecondary: {
    padding: 14,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#c0392b",
    alignItems: "center",
  },
  btnSecondaryText: { color: "#c0392b", fontWeight: "600", fontSize: 15 },
  btnLink: { padding: 14, alignItems: "center" },
  btnLinkText: { color: "#2c3e50" },
});
