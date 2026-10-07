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

import { ApiError } from "../../api/client";
import { createLedger } from "../../api/ledgers";
import { normalizeMoneyInput } from "../../utils/money";

export default function LedgerCreateScreen({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const [name, setName] = useState("");
  const [groupName, setGroupName] = useState("");
  const [openingBalance, setOpeningBalance] = useState("");
  const [balanceType, setBalanceType] = useState<"Dr" | "Cr">("Dr");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim().length > 0 && !submitting;

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      await createLedger({
        name: name.trim(),
        group_name: groupName.trim() === "" ? null : groupName.trim(),
        opening_balance: normalizeMoneyInput(openingBalance) ?? "0.00",
        balance_type: balanceType,
      });
      onCreated();
    } catch (exc) {
      if (exc instanceof ApiError) {
        setError(exc.message);
      } else {
        setError("Could not create the ledger. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>New ledger</Text>
      <Text style={styles.hint}>
        Pushed to Tally automatically. Any voucher using it is held as
        "Optional" in Tally until the push is confirmed, so it's always
        reviewed at the Tally machine first.
      </Text>

      <TextInput
        accessibilityLabel="ledger-name"
        placeholder="Name"
        value={name}
        onChangeText={setName}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="ledger-group"
        placeholder="Group (e.g. Sundry Debtors)"
        value={groupName}
        onChangeText={setGroupName}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="ledger-opening-balance"
        placeholder="Opening balance (optional)"
        value={openingBalance}
        onChangeText={setOpeningBalance}
        keyboardType="decimal-pad"
        style={styles.input}
      />
      <View style={styles.typeRow}>
        {(["Dr", "Cr"] as const).map((t) => {
          const active = t === balanceType;
          return (
            <Pressable
              key={t}
              accessibilityRole="button"
              accessibilityLabel={`pick-balance-type-${t}`}
              accessibilityState={{ selected: active }}
              onPress={() => setBalanceType(t)}
              style={({ pressed }) => [
                styles.typeChip,
                active && styles.typeChipActive,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text
                style={[
                  styles.typeChipText,
                  active && styles.typeChipTextActive,
                ]}
              >
                {t}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {error !== null && <Text style={styles.error}>{error}</Text>}

      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="cancel"
          onPress={onCancel}
          style={({ pressed }) => [
            styles.btn,
            styles.btnSecondary,
            pressed && { opacity: 0.85 },
          ]}
        >
          <Text style={styles.btnSecondaryText}>Cancel</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="create-ledger"
          onPress={onSubmit}
          disabled={!canSubmit}
          style={({ pressed }) => [
            styles.btn,
            !canSubmit && styles.btnDisabled,
            pressed && { opacity: 0.85 },
          ]}
        >
          {submitting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.btnText}>Create ledger</Text>
          )}
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, gap: 10 },
  title: { fontSize: 24, fontWeight: "600" },
  hint: { color: "#7f8c8d", fontSize: 13 },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  typeRow: { flexDirection: "row", gap: 8, paddingVertical: 4 },
  typeChip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#bdc3c7",
    backgroundColor: "#fff",
  },
  typeChipActive: { backgroundColor: "#2c3e50", borderColor: "#2c3e50" },
  typeChipText: { color: "#2c3e50", fontWeight: "600" },
  typeChipTextActive: { color: "#fff" },
  error: { color: "#c0392b" },
  actions: { flexDirection: "row", gap: 12, marginTop: 12 },
  btn: {
    flex: 1,
    backgroundColor: "#2c3e50",
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: "center",
  },
  btnDisabled: { backgroundColor: "#95a5a6" },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 16 },
  btnSecondary: { backgroundColor: "#fff", borderWidth: 1, borderColor: "#bdc3c7" },
  btnSecondaryText: { color: "#2c3e50", fontWeight: "600", fontSize: 16 },
});
