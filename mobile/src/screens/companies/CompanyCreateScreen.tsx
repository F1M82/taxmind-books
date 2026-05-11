import React, { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { createCompany } from "../../api/companies";
import { ApiError } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";

export default function CompanyCreateScreen({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const { refreshMe } = useAuth();
  const { setActive } = useActiveCompany();
  const [name, setName] = useState("");
  const [gstin, setGstin] = useState("");
  const [state, setStateCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (): Promise<void> => {
    setError(null);
    setSubmitting(true);
    try {
      const created = await createCompany({
        name: name.trim(),
        gstin: gstin.trim() === "" ? null : gstin.trim().toUpperCase(),
        state_code: state.trim() === "" ? null : state.trim(),
      });
      // Refresh /me so the membership shows up in the user.companies
      // list, then make the new company active.
      await refreshMe();
      await setActive(created.id);
      onCreated();
    } catch (exc) {
      if (
        exc instanceof ApiError &&
        exc.code === "gstin_already_registered"
      ) {
        setError("That GSTIN is already registered to another company.");
      } else if (exc instanceof ApiError && exc.code === "validation_error") {
        setError("Please check the GSTIN / state code format.");
      } else {
        setError("Could not create the company. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Create a company</Text>
      <TextInput
        accessibilityLabel="company-name"
        placeholder="Company name"
        value={name}
        onChangeText={setName}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="gstin"
        placeholder="GSTIN (optional, 15 chars)"
        autoCapitalize="characters"
        autoCorrect={false}
        value={gstin}
        onChangeText={setGstin}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="state-code"
        placeholder="State code (optional, e.g. 27)"
        keyboardType="number-pad"
        value={state}
        onChangeText={setStateCode}
        style={styles.input}
      />
      {error !== null && <Text style={styles.error}>{error}</Text>}
      <View style={styles.row}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="cancel"
          onPress={onCancel}
          style={({ pressed }) => [
            styles.button,
            styles.buttonSecondary,
            pressed && { opacity: 0.85 },
          ]}
        >
          <Text style={styles.buttonSecondaryText}>Cancel</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="create"
          onPress={onSubmit}
          disabled={submitting || name.length === 0}
          style={({ pressed }) => [
            styles.button,
            (submitting || name.length === 0) && styles.buttonDisabled,
            pressed && { opacity: 0.85 },
          ]}
        >
          {submitting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Create</Text>
          )}
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, gap: 12 },
  title: { fontSize: 24, fontWeight: "600" },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  error: { color: "#c0392b" },
  row: { flexDirection: "row", gap: 12, marginTop: 8 },
  button: {
    flex: 1,
    backgroundColor: "#2c3e50",
    padding: 14,
    borderRadius: 8,
    alignItems: "center",
  },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  buttonDisabled: { backgroundColor: "#95a5a6" },
  buttonSecondary: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: "#2c3e50",
  },
  buttonSecondaryText: { color: "#2c3e50", fontWeight: "600", fontSize: 16 },
});
