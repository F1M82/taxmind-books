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

import {
  OnboardingChecklistResponse,
  OnboardingItem,
  OnboardingItemKey,
  getOnboardingChecklist,
} from "../../api/onboarding";

/**
 * Onboarding checklist screen (P0.43).
 *
 * Renders all five items from /onboarding/checklist with their
 * current completion state. Tapping an incomplete item navigates to
 * the relevant flow; completed and out-of-scope items are inert.
 *
 * Per-item navigation (Phase 0):
 *   - company_created           → inert, always done.
 *   - connector_installed       → inert. The connector ships as a
 *                                 Windows .exe; there's no mobile
 *                                 install flow. The subtitle tells
 *                                 the user where to look.
 *   - ledgers_synced            → Ledgers screen (the user can see
 *                                 the current ledger list and the
 *                                 sync button lives there once it
 *                                 lands).
 *   - first_voucher_posted      → NewVoucher.
 *   - first_invoice_extracted   → inert; Phase 1+.
 */
export default function OnboardingScreen({
  onOpenLedgers,
  onOpenNewVoucher,
}: {
  onOpenLedgers: () => void;
  onOpenNewVoucher: () => void;
}): React.ReactElement {
  const [data, setData] = useState<OnboardingChecklistResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      setData(await getOnboardingChecklist());
    } catch {
      setError("Could not load onboarding checklist.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const tapHandlerFor = (key: OnboardingItemKey): (() => void) | undefined => {
    switch (key) {
      case "ledgers_synced":
        return onOpenLedgers;
      case "first_voucher_posted":
        return onOpenNewVoucher;
      default:
        return undefined;
    }
  };

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} />
      }
    >
      {data === null && error === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}
      {error !== null && <Text style={styles.error}>{error}</Text>}

      {data !== null && (
        <>
          <Text style={styles.heading}>Set up your account</Text>
          <Text style={styles.progress}>
            {data.completed_count} of {data.total_count} complete
          </Text>

          {data.items.map((item) => (
            <ItemRow
              key={item.key}
              item={item}
              onPress={tapHandlerFor(item.key)}
              extraSubtitle={subtitleFor(item.key)}
            />
          ))}
        </>
      )}
    </ScrollView>
  );
}

function ItemRow({
  item,
  onPress,
  extraSubtitle,
}: {
  item: OnboardingItem;
  onPress?: () => void;
  extraSubtitle?: string;
}): React.ReactElement {
  const tappable = onPress !== undefined && !item.completed;
  const body = (
    <View
      accessibilityLabel={`item-${item.key}`}
      style={[
        styles.row,
        item.completed ? styles.rowDone : styles.rowPending,
      ]}
    >
      <View style={styles.iconCol}>
        <Text style={[styles.tick, item.completed && styles.tickDone]}>
          {item.completed ? "✓" : "○"}
        </Text>
      </View>
      <View style={styles.rowMain}>
        <Text style={styles.label}>{item.label}</Text>
        {item.completed && item.completed_at !== undefined && (
          <Text style={styles.completedAt}>
            Done {new Date(item.completed_at).toLocaleDateString()}
          </Text>
        )}
        {!item.completed && extraSubtitle !== undefined && (
          <Text style={styles.subtitle}>{extraSubtitle}</Text>
        )}
      </View>
      {tappable && <Text style={styles.chevron}>›</Text>}
    </View>
  );

  if (!tappable) {
    return body;
  }
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`open-${item.key}`}
      onPress={onPress}
      style={({ pressed }) => [pressed && { opacity: 0.85 }]}
    >
      {body}
    </Pressable>
  );
}

function subtitleFor(key: OnboardingItemKey): string | undefined {
  switch (key) {
    case "connector_installed":
      return "Download the Tally Connector on your PC and pair it from Settings";
    case "first_invoice_extracted":
      return "Coming in Phase 1";
    case "ledgers_synced":
      return "Pull ledgers from TallyPrime";
    case "first_voucher_posted":
      return "Create your first manual voucher";
    default:
      return undefined;
  }
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 10 },
  center: { padding: 24, alignItems: "center" },
  heading: { fontSize: 20, fontWeight: "700", marginBottom: 2 },
  progress: { fontSize: 13, color: "#666", marginBottom: 8 },
  row: {
    flexDirection: "row",
    padding: 14,
    borderRadius: 8,
    borderWidth: 1,
    alignItems: "center",
    gap: 12,
  },
  rowDone: { backgroundColor: "#eafaf1", borderColor: "#27ae60" },
  rowPending: { backgroundColor: "#fff", borderColor: "#e0e0e0" },
  iconCol: { width: 24, alignItems: "center" },
  tick: { fontSize: 20, color: "#bbb" },
  tickDone: { color: "#27ae60" },
  rowMain: { flex: 1, gap: 2 },
  label: { fontSize: 15, fontWeight: "600", color: "#222" },
  completedAt: { fontSize: 12, color: "#666" },
  subtitle: { fontSize: 12, color: "#666" },
  chevron: { fontSize: 22, color: "#999" },
  error: { color: "#c0392b", padding: 16 },
});
