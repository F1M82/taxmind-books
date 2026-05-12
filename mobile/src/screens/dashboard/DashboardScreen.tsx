import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";
import ConnectorStatusCard from "./ConnectorStatusCard";

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
  const hasActive = activeCompanyId !== null;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Welcome, {user.full_name}</Text>
      <Text style={styles.line}>{user.email}</Text>

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

      {hasActive && <ConnectorStatusCard />}

      {hasActive && (
        <>
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
              accessibilityLabel="open-profit-loss"
              onPress={onOpenProfitLoss}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Profit &amp; Loss</Text>
              <Text style={styles.shortcutSubtitle}>Income vs expense</Text>
            </Pressable>
          </View>
          <View style={styles.shortcuts}>
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
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="open-outstanding"
              onPress={onOpenOutstanding}
              style={({ pressed }) => [
                styles.shortcut,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.shortcutTitle}>Outstanding</Text>
              <Text style={styles.shortcutSubtitle}>
                Receivables / payables
              </Text>
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
  container: { padding: 24, gap: 12 },
  title: { fontSize: 24, fontWeight: "600" },
  line: { fontSize: 16, color: "#444" },
  companyChip: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#2c3e50",
    borderRadius: 8,
    marginTop: 12,
    gap: 2,
  },
  companyChipLabel: { fontSize: 12, color: "#666", letterSpacing: 1 },
  companyChipValue: { fontSize: 16, fontWeight: "600", color: "#2c3e50" },
  shortcuts: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
  },
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
  sectionLabel: {
    fontSize: 12,
    color: "#666",
    letterSpacing: 1,
    marginTop: 12,
  },
  signOut: {
    marginTop: 24,
    padding: 12,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#c0392b",
    borderRadius: 8,
  },
  signOutText: { color: "#c0392b", fontSize: 16 },
});
