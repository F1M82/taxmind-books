import React, { useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { LedgerListItem } from "../../api/ledgers";
import { createVoucher, VoucherType } from "../../api/vouchers";
import { formatINR, normalizeMoneyInput } from "../../utils/money";
import LedgerListScreen from "../ledgers/LedgerListScreen";

interface EntryDraft {
  ledger: LedgerListItem | null;
  amount: string;
  entry_type: "Dr" | "Cr";
}

function _uuid(): string {
  // RFC4122 v4 via crypto.getRandomValues (Expo / RN has it).
  const bytes = new Uint8Array(16);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
    .slice(6, 8)
    .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
}

export default function VoucherEntryScreen({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const [voucherType] = useState<VoucherType>("Receipt"); // Phase-0 P0.31: Receipt only; P0.36 broadens.
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [narration, setNarration] = useState("");
  const [reference, setReference] = useState("");
  const [entries, setEntries] = useState<EntryDraft[]>([
    { ledger: null, amount: "", entry_type: "Dr" },
    { ledger: null, amount: "", entry_type: "Cr" },
  ]);
  const [pickingIndex, setPickingIndex] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const updateEntry = (idx: number, patch: Partial<EntryDraft>) => {
    setEntries((prev) =>
      prev.map((e, i) => (i === idx ? { ...e, ...patch } : e)),
    );
  };

  const sumDr = entries
    .filter((e) => e.entry_type === "Dr")
    .reduce((acc, e) => acc + (Number(normalizeMoneyInput(e.amount) ?? 0)), 0);
  const sumCr = entries
    .filter((e) => e.entry_type === "Cr")
    .reduce((acc, e) => acc + (Number(normalizeMoneyInput(e.amount) ?? 0)), 0);
  const balanced = sumDr > 0 && sumDr === sumCr;
  const allLedgersPicked = entries.every((e) => e.ledger !== null);
  const allAmountsValid = entries.every(
    (e) => normalizeMoneyInput(e.amount) !== null,
  );
  const canSubmit =
    balanced && allLedgersPicked && allAmountsValid && !submitting;

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const normEntries = entries.map((e) => ({
        ledger_id: e.ledger!.id,
        amount: normalizeMoneyInput(e.amount)!,
        entry_type: e.entry_type,
      }));
      const total = normalizeMoneyInput(String(sumDr.toFixed(2)))!;
      await createVoucher(
        {
          voucher_type: voucherType,
          date,
          narration: narration.trim() === "" ? null : narration.trim(),
          reference: reference.trim() === "" ? null : reference.trim(),
          total_amount: total,
          entries: normEntries,
          gst_applicable: false,
        },
        _uuid(),
      );
      onCreated();
    } catch (exc) {
      if (exc instanceof ApiError) {
        if (exc.code === "voucher_entries_unbalanced") {
          setError("Dr and Cr totals must match.");
        } else if (exc.code === "ledger_not_found") {
          setError("A selected ledger no longer exists in this company.");
        } else if (exc.code === "validation_error") {
          setError("Some values failed validation. Check amounts and dates.");
        } else {
          setError(exc.message);
        }
      } else {
        setError("Could not create the voucher. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>New {voucherType}</Text>

      <TextInput
        accessibilityLabel="voucher-date"
        placeholder="YYYY-MM-DD"
        value={date}
        onChangeText={setDate}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="voucher-narration"
        placeholder="Narration"
        value={narration}
        onChangeText={setNarration}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="voucher-reference"
        placeholder="Reference (UTR / cheque no)"
        value={reference}
        onChangeText={setReference}
        style={styles.input}
      />

      <Text style={styles.section}>Entries</Text>
      {entries.map((e, idx) => (
        <View key={idx} style={styles.entry}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`pick-ledger-${idx}`}
            onPress={() => setPickingIndex(idx)}
            style={({ pressed }) => [
              styles.ledgerPicker,
              pressed && { opacity: 0.85 },
            ]}
          >
            <Text style={styles.ledgerPickerText}>
              {e.ledger?.name ?? "Pick ledger"}
            </Text>
          </Pressable>
          <View style={styles.entryRow}>
            <TextInput
              accessibilityLabel={`entry-amount-${idx}`}
              placeholder="0.00"
              keyboardType="decimal-pad"
              value={e.amount}
              onChangeText={(v) => updateEntry(idx, { amount: v })}
              style={[styles.input, { flex: 1 }]}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`toggle-type-${idx}`}
              onPress={() =>
                updateEntry(idx, {
                  entry_type: e.entry_type === "Dr" ? "Cr" : "Dr",
                })
              }
              style={({ pressed }) => [
                styles.typeToggle,
                e.entry_type === "Dr" ? styles.typeDr : styles.typeCr,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.typeToggleText}>{e.entry_type}</Text>
            </Pressable>
          </View>
        </View>
      ))}

      <View style={styles.totalsBox}>
        <Text style={styles.totalsLine}>Dr total: {formatINR(String(sumDr.toFixed(2)))}</Text>
        <Text style={styles.totalsLine}>Cr total: {formatINR(String(sumCr.toFixed(2)))}</Text>
        {!balanced && sumDr + sumCr > 0 && (
          <Text style={styles.error}>Totals do not match yet.</Text>
        )}
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
          accessibilityLabel="post"
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
            <Text style={styles.btnText}>Post voucher</Text>
          )}
        </Pressable>
      </View>

      <Modal
        animationType="slide"
        visible={pickingIndex !== null}
        onRequestClose={() => setPickingIndex(null)}
      >
        <View style={styles.modalHeader}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="close-picker"
            onPress={() => setPickingIndex(null)}
          >
            <Text style={styles.modalClose}>Cancel</Text>
          </Pressable>
        </View>
        <LedgerListScreen
          onPickLedger={(led) => {
            if (pickingIndex !== null) {
              updateEntry(pickingIndex, { ledger: led });
            }
            setPickingIndex(null);
          }}
        />
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, gap: 10 },
  title: { fontSize: 24, fontWeight: "600" },
  section: { fontSize: 18, fontWeight: "600", marginTop: 12 },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  entry: {
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    padding: 12,
    gap: 8,
  },
  ledgerPicker: {
    padding: 12,
    borderRadius: 6,
    backgroundColor: "#f0f3f5",
  },
  ledgerPickerText: { fontSize: 16 },
  entryRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  typeToggle: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 6,
  },
  typeDr: { backgroundColor: "#27ae60" },
  typeCr: { backgroundColor: "#c0392b" },
  typeToggleText: { color: "#fff", fontWeight: "700" },
  totalsBox: {
    marginTop: 8,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "#fafafa",
  },
  totalsLine: { fontSize: 14 },
  error: { color: "#c0392b" },
  actions: { flexDirection: "row", gap: 12, marginTop: 12 },
  btn: {
    flex: 1,
    backgroundColor: "#2c3e50",
    padding: 14,
    borderRadius: 8,
    alignItems: "center",
  },
  btnText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  btnDisabled: { backgroundColor: "#95a5a6" },
  btnSecondary: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderColor: "#2c3e50",
  },
  btnSecondaryText: { color: "#2c3e50", fontWeight: "600", fontSize: 16 },
  modalHeader: {
    paddingTop: 48,
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#e0e0e0",
  },
  modalClose: { color: "#2980b9", fontSize: 16 },
});
