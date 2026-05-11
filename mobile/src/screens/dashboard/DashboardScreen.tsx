import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";
import ConnectorStatusCard from "./ConnectorStatusCard";

export default function DashboardScreen({
  onOpenCompanies,
}: {
  onOpenCompanies: () => void;
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

      {activeCompanyId !== null && <ConnectorStatusCard />}

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
