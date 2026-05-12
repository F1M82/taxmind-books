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

import { DashboardHomeResponse, getDashboardHome } from "../../api/dashboard";
import AlertsList from "../../components/dashboard/AlertsList";
import ConnectorTile from "../../components/dashboard/ConnectorTile";
import GstTile from "../../components/dashboard/GstTile";
import MetricsTile from "../../components/dashboard/MetricsTile";
import OutstandingTile from "../../components/dashboard/OutstandingTile";
import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";

export default function DashboardScreen({
  onOpenCompanies,
  onOpenLedgers,
  onOpenVouchers,
  onOpenTrialBalance,
  onOpenProfitLoss,
  onOpenBalanceSheet,
  onOpenOutstanding,
}: {
  onOpenCompanies: () => void;
  onOpenLedgers: () => void;
  onOpenVouchers: () => void;
  onOpenTrialBalance: () => void;
  onOpenProfitLoss: () => void;
  onOpenBalanceSheet: () => void;
  onOpenOutstanding: () => void;
}): React.ReactElement {
  const { user, signOut } = useAuth();
  const { activeCompanyId, loading: companyLoading } = useActiveCompany();
  const hasActive = activeCompanyId !== null;

  const [data, setData] = useState<DashboardHomeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    if (!hasActive) {
      setData(null);
      return;
    }
    setError(null);
    setRefreshing(true);
    try {
      setData(await getDashboardHome());
    } catch {
      setError("Could not load dashboard.");
    } finally {
      setRefreshing(false);
    }
  }, [hasActive]);

  useEffect(() => {
    void load();
  }, [load]);

  if (user === null) {
    return (
      <View style={styles.container}>
        <Text>Not signed in.</Text>
      </View>
    );
  }

  const activeCompany =
    activeCompanyId === null
      ? null
      : user.companies.find((c) => c.id === activeCompanyId) ?? null;

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} />
      }
    >
      <Text style={styles.title}>Welcome, {user.full_name}</Text>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="open-companies"
        onPress={onOpenCompanies}
        style={({ pressed }) => [
          styles.companyChip,
          pressed && { opacity: 0.85 },
        ]}
      >
        <Text style={styles.companyChipLabel}>ACTIVE COMPANY</Text>
        <Text style={styles.companyChipValue}>
          {companyLoading
            ? "Loading…"
            : activeCompany !== null
            ? `${activeCompany.name} · ${activeCompany.role}`
            : "Tap to create your first company"}
        </Text>
      </Pressable>

      {hasActive && data === null && error === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}

      {hasActive && error !== null && (
        <Text style={styles.error}>{error}</Text>
      )}

      {hasActive && data !== null && (
        <>
          <AlertsList
            alerts={data.alerts}
            onPressAlert={(a) => {
              if (a.kind === "pending_approvals") onOpenVouchers();
            }}
          />

          <View style={styles.tileRow}>
            <ConnectorTile connector={data.connector} />
          </View>

          <View style={styles.tileRow}>
            <MetricsTile
              label="TODAY"
              vouchersCreated={data.today.vouchers_created}
              pendingApproval={data.today.vouchers_pending_approval}
              cashIn={data.today.cash_in}
              cashOut={data.today.cash_out}
              onPress={onOpenVouchers}
              accessibilityLabel="tile-today"
            />
            <MetricsTile
              label="THIS MONTH"
              vouchersCreated={data.this_month.vouchers_created}
              pendingApproval={data.this_month.vouchers_pending_approval}
              cashIn={data.this_month.cash_in}
              cashOut={data.this_month.cash_out}
              onPress={onOpenProfitLoss}
              accessibilityLabel="tile-this-month"
            />
          </View>

          <View style={styles.tileRow}>
            <OutstandingTile
              outstanding={data.outstanding}
              onPress={onOpenOutstanding}
            />
            <GstTile gst={data.gst_liability_indicative} />
          </View>

          <Text style={styles.sectionLabel}>SHORTCUTS</Text>
          <View style={styles.shortcuts}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open-ledgers"
              onPress={onOpenLedgers}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Ledgers</Text>
              <Text style={styles.shortcutSubtitle}>
                Browse + fuzzy search
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open-vouchers"
              onPress={onOpenVouchers}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Vouchers</Text>
              <Text style={styles.shortcutSubtitle}>
                Create + Tally status
              </Text>
            </Pressable>
          </View>

          <Text style={styles.sectionLabel}>REPORTS</Text>
          <View style={styles.shortcuts}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open-trial-balance"
              onPress={onOpenTrialBalance}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Trial Balance</Text>
              <Text style={styles.shortcutSubtitle}>As-of snapshot</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open-balance-sheet"
              onPress={onOpenBalanceSheet}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Balance Sheet</Text>
              <Text style={styles.shortcutSubtitle}>Assets vs liabilities</Text>
            </Pressable>
          </View>
        </>
      )}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="sign-out"
        onPress={signOut}
        style={({ pressed }) => [styles.signOut, pressed && { opacity: 0.85 }]}
      >
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 10 },
  center: { padding: 24, alignItems: "center" },
  title: { fontSize: 22, fontWeight: "600" },
  companyChip: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#2c3e50",
    borderRadius: 8,
    gap: 2,
  },
  companyChipLabel: { fontSize: 12, color: "#666", letterSpacing: 1 },
  companyChipValue: { fontSize: 16, fontWeight: "600", color: "#2c3e50" },
  tileRow: { flexDirection: "row", gap: 10 },
  sectionLabel: {
    fontSize: 12,
    color: "#666",
    letterSpacing: 1,
    marginTop: 8,
  },
  shortcuts: { flexDirection: "row", gap: 10 },
  shortcut: {
    flex: 1,
    padding: 14,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    gap: 4,
  },
  shortcutTitle: { fontSize: 16, fontWeight: "600" },
  shortcutSubtitle: { fontSize: 12, color: "#666" },
  signOut: {
    marginTop: 16,
    padding: 12,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#c0392b",
    borderRadius: 8,
  },
  signOutText: { color: "#c0392b", fontSize: 16 },
  error: { color: "#c0392b" },
});
