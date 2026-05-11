import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAuth } from "../../context/AuthContext";

export default function DashboardScreen(): React.ReactElement {
  const { user, signOut } = useAuth();
  if (user === null) {
    return (
      <View style={styles.container}>
        <Text>Not signed in.</Text>
      </View>
    );
  }
  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Welcome, {user.full_name}</Text>
      <Text style={styles.line}>{user.email}</Text>
      {user.is_ca && user.firm_name !== null && (
        <Text style={styles.line}>{user.firm_name}</Text>
      )}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Companies</Text>
        {user.companies.length === 0 ? (
          <Text>You don't belong to any company yet.</Text>
        ) : (
          user.companies.map((c) => (
            <View key={c.id} style={styles.companyRow}>
              <Text style={styles.companyName}>{c.name}</Text>
              <Text style={styles.companyRole}>{c.role}</Text>
            </View>
          ))
        )}
      </View>

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
  card: {
    marginTop: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 8,
  },
  cardTitle: { fontSize: 18, fontWeight: "600", marginBottom: 4 },
  companyRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
  },
  companyName: { fontSize: 16 },
  companyRole: { fontSize: 14, color: "#666" },
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
