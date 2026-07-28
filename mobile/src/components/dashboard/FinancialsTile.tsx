import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  DashboardFinancialsResponse,
  getDashboardFinancials,
} from "../../api/dashboard";
import { ApiError } from "../../api/client";
import { formatINR } from "../../utils/money";

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

// Indian FY starts April 1. getMonth() is 0-indexed (March === 2).
function fyStart(d: Date): Date {
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return new Date(y, 3, 1);
}

function quarterStart(d: Date): Date {
  // FY quarters: Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar.
  const m = d.getMonth();
  const startMonth = m >= 3 ? m - ((m - 3) % 3) : m - ((m + 9) % 3);
  const y =
    startMonth < 0 ? d.getFullYear() - 1 : d.getFullYear();
  return new Date(y, (startMonth + 12) % 12, 1);
}

type PresetKey = "fy" | "quarter" | "month" | "lastfy" | "custom";

interface Period {
  from: string;
  to: string;
}

function presetPeriod(key: Exclude<PresetKey, "custom">): Period {
  const today = new Date();
  const to = iso(today);
  if (key === "fy") return { from: iso(fyStart(today)), to };
  if (key === "quarter") return { from: iso(quarterStart(today)), to };
  if (key === "month")
    return {
      from: iso(new Date(today.getFullYear(), today.getMonth(), 1)),
      to,
    };
  // lastfy: the FY before the current one, full April 1 → March 31.
  const thisFy = fyStart(today);
  const lastFyStart = new Date(thisFy.getFullYear() - 1, 3, 1);
  const lastFyEnd = new Date(thisFy.getFullYear(), 2, 31);
  return { from: iso(lastFyStart), to: iso(lastFyEnd) };
}

const PRESETS: { key: Exclude<PresetKey, "custom">; label: string }[] = [
  { key: "fy", label: "This FY" },
  { key: "quarter", label: "This Quarter" },
  { key: "month", label: "This Month" },
  { key: "lastfy", label: "Last FY" },
];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export default function FinancialsTile(): React.ReactElement {
  const [preset, setPreset] = useState<PresetKey>("fy");
  // Custom-range text inputs, seeded from the current FY.
  const initialFy = useMemo(() => presetPeriod("fy"), []);
  const [customFrom, setCustomFrom] = useState(initialFy.from);
  const [customTo, setCustomTo] = useState(initialFy.to);

  const [data, setData] = useState<DashboardFinancialsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const period: Period | null = useMemo(() => {
    if (preset === "custom") {
      if (!DATE_RE.test(customFrom) || !DATE_RE.test(customTo)) return null;
      return { from: customFrom, to: customTo };
    }
    return presetPeriod(preset);
  }, [preset, customFrom, customTo]);

  const load = useCallback(async (p: Period) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getDashboardFinancials(p.from, p.to));
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not load financials.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (period !== null) void load(period);
  }, [period, load]);

  const netPositive = data?.net_profit.type === "profit";

  return (
    <View style={styles.card}>
      <Text style={styles.heading}>FINANCIALS</Text>

      <View style={styles.presetRow}>
        {PRESETS.map((p) => (
          <Pressable
            key={p.key}
            accessibilityLabel={`period-${p.key}`}
            onPress={() => setPreset(p.key)}
            style={[styles.chip, preset === p.key && styles.chipActive]}
          >
            <Text
              style={[
                styles.chipText,
                preset === p.key && styles.chipTextActive,
              ]}
            >
              {p.label}
            </Text>
          </Pressable>
        ))}
        <Pressable
          accessibilityLabel="period-custom"
          onPress={() => setPreset("custom")}
          style={[styles.chip, preset === "custom" && styles.chipActive]}
        >
          <Text
            style={[
              styles.chipText,
              preset === "custom" && styles.chipTextActive,
            ]}
          >
            Custom
          </Text>
        </Pressable>
      </View>

      {preset === "custom" && (
        <View style={styles.customRow}>
          <TextInput
            accessibilityLabel="custom-from"
            style={styles.dateInput}
            placeholder="YYYY-MM-DD"
            autoCapitalize="none"
            value={customFrom}
            onChangeText={setCustomFrom}
          />
          <Text style={styles.dash}>→</Text>
          <TextInput
            accessibilityLabel="custom-to"
            style={styles.dateInput}
            placeholder="YYYY-MM-DD"
            autoCapitalize="none"
            value={customTo}
            onChangeText={setCustomTo}
          />
        </View>
      )}

      {period !== null && (
        <Text style={styles.periodLabel}>
          {period.from} → {period.to}
        </Text>
      )}
      {preset === "custom" && period === null && (
        <Text style={styles.hint}>Enter both dates as YYYY-MM-DD.</Text>
      )}

      {loading && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}
      {error !== null && <Text style={styles.error}>{error}</Text>}

      {data !== null && !loading && error === null && (
        <View style={styles.grid}>
          <View style={styles.cell}>
            <Text style={styles.cellLabel}>Sales</Text>
            <Text style={styles.cellValue}>{formatINR(data.sales)}</Text>
          </View>
          <View style={styles.cell}>
            <Text style={styles.cellLabel}>Purchase</Text>
            <Text style={styles.cellValue}>{formatINR(data.purchase)}</Text>
          </View>
          <View style={styles.cell}>
            <Text style={styles.cellLabel}>Expenses</Text>
            <Text style={styles.cellValue}>{formatINR(data.expenses)}</Text>
          </View>
          <View style={styles.cell}>
            <Text style={styles.cellLabel}>
              Net {netPositive ? "Profit" : "Loss"}
            </Text>
            <Text
              style={[
                styles.cellValue,
                netPositive ? styles.profit : styles.loss,
              ]}
            >
              {formatINR(data.net_profit.value)}
            </Text>
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 10,
  },
  heading: { fontSize: 12, color: "#666", letterSpacing: 1 },
  presetRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#ccc",
  },
  chipActive: { backgroundColor: "#2c3e50", borderColor: "#2c3e50" },
  chipText: { color: "#333", fontSize: 13 },
  chipTextActive: { color: "#fff", fontWeight: "600" },
  customRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dateInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 10,
    fontSize: 14,
  },
  dash: { color: "#666" },
  periodLabel: { fontSize: 12, color: "#666" },
  hint: { fontSize: 12, color: "#c0392b" },
  center: { padding: 16, alignItems: "center" },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  cell: {
    flexBasis: "47%",
    flexGrow: 1,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "#f7f9fa",
    gap: 4,
  },
  cellLabel: { fontSize: 12, color: "#666" },
  cellValue: { fontSize: 18, fontWeight: "700", color: "#222" },
  profit: { color: "#27ae60" },
  loss: { color: "#c0392b" },
  error: { color: "#c0392b" },
});
