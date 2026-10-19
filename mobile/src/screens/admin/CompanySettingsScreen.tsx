/**
 * Owner/admin: set `default_purchase_ledger_id`, the single per-company
 * Purchase ledger the Phase 1 invoice-scan pipeline debits when
 * auto-posting a matched ingestion (see CLAUDE.md "Auto-creating the
 * Voucher"). Previously API-only (PATCH /companies/{id}).
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { CompanyOut, getCompany, updateCompany } from "../../api/companies";
import { LedgerListItem, listLedgers } from "../../api/ledgers";
import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";
import LedgerListScreen from "../ledgers/LedgerListScreen";

export default function CompanySettingsScreen(): React.ReactElement {
  const { user } = useAuth();
  const { activeCompanyId } = useActiveCompany();
  const [company, setCompany] = useState<CompanyOut | null>(null);
  const [defaultLedgerName, setDefaultLedgerName] = useState<string | null>(
    null,
  );
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeCompany =
    activeCompanyId === null
      ? null
      : (user?.companies.find((c) => c.id === activeCompanyId) ?? null);
  const canEdit =
    activeCompany?.role === "owner" || activeCompany?.role === "admin";

  const load = useCallback(async () => {
    if (activeCompanyId === null) return;
    setError(null);
    try {
      const c = await getCompany(activeCompanyId);
      setCompany(c);
      if (c.default_purchase_ledger_id !== null) {
        // The company record only carries the ledger id -- resolve its
        // name once so the screen doesn't show a raw UUID.
        const { items } = await listLedgers();
        const match = items.find(
          (l) => l.id === c.default_purchase_ledger_id,
        );
        setDefaultLedgerName(match?.name ?? c.default_purchase_ledger_id);
      } else {
        setDefaultLedgerName(null);
      }
    } catch {
      setError("Could not load company settings.");
    }
  }, [activeCompanyId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onPick = useCallback(
    async (ledger: LedgerListItem) => {
      if (activeCompanyId === null) return;
      setPicking(false);
      setSaving(true);
      setError(null);
      try {
        const updated = await updateCompany(activeCompanyId, {
          default_purchase_ledger_id: ledger.id,
        });
        setCompany(updated);
        setDefaultLedgerName(ledger.name);
      } catch (e) {
        setError(
          e instanceof ApiError ? e.message : "Could not save the setting.",
        );
      } finally {
        setSaving(false);
      }
    },
    [activeCompanyId],
  );

  if (!canEdit) {
    return (
      <View style={styles.center}>
        <Text style={styles.note}>
          Only an owner or admin can change company settings.
        </Text>
      </View>
    );
  }

  if (company === null && error === null) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.sectionLabel}>DEFAULT PURCHASE LEDGER</Text>
      <Text style={styles.hint}>
        Used to auto-build a voucher from a scanned invoice (debit this
        ledger, credit the matched vendor). Leave unset to always require a
        manual review.
      </Text>
      {error !== null && <Text style={styles.error}>{error}</Text>}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="pick-default-purchase-ledger"
        disabled={saving}
        onPress={() => setPicking(true)}
        style={({ pressed }) => [
          styles.picker,
          pressed && { opacity: 0.85 },
        ]}
      >
        {saving ? (
          <ActivityIndicator />
        ) : (
          <Text style={styles.pickerText}>
            {defaultLedgerName !== null
              ? defaultLedgerName
              : "Not set — tap to choose"}
          </Text>
        )}
      </Pressable>

      <Modal
        animationType="slide"
        visible={picking}
        onRequestClose={() => setPicking(false)}
      >
        <View style={styles.modalHeader}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="close-picker"
            onPress={() => setPicking(false)}
          >
            <Text style={styles.modalClose}>Cancel</Text>
          </Pressable>
        </View>
        <LedgerListScreen onPickLedger={(led) => void onPick(led)} />
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, gap: 10 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  sectionLabel: { fontSize: 12, color: "#666", letterSpacing: 1 },
  hint: { fontSize: 13, color: "#444", lineHeight: 18 },
  picker: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#2c3e50",
    borderRadius: 8,
    alignItems: "center",
  },
  pickerText: { fontSize: 15, fontWeight: "600", color: "#2c3e50" },
  modalHeader: { padding: 16, alignItems: "flex-end" },
  modalClose: { fontSize: 16, color: "#2c3e50" },
  note: { color: "#666", fontSize: 14, textAlign: "center" },
  error: { color: "#c0392b" },
});
