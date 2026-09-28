import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { FinancialYear } from "../../api/reports";

interface Props {
  periods: FinancialYear[];
  /** Label of the selected year, or null when a custom date is in use. */
  selectedLabel: string | null;
  onSelect: (period: FinancialYear) => void;
}

/**
 * Horizontal financial-year chips ("FY 2025-26", newest first) — the mobile
 * counterpart of choosing the period in Tally. Hidden when there is only one
 * year to choose from.
 */
export default function FinancialYearPicker({
  periods,
  selectedLabel,
  onSelect,
}: Props): React.ReactElement | null {
  if (periods.length < 2) {
    return null;
  }
  return (
    <View style={styles.wrap}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
      >
        {periods.map((p) => {
          const selected = p.label === selectedLabel;
          return (
            <Pressable
              key={p.label}
              accessibilityRole="button"
              accessibilityLabel={`fy-${p.label}`}
              accessibilityState={{ selected }}
              onPress={() => onSelect(p)}
              style={[styles.chip, selected && styles.chipSelected]}
            >
              <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                {p.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
    paddingVertical: 8,
  },
  row: { paddingHorizontal: 12, gap: 8 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#2c3e50",
  },
  chipSelected: { backgroundColor: "#2c3e50" },
  chipText: { fontSize: 13, color: "#2c3e50", fontWeight: "600" },
  chipTextSelected: { color: "#fff" },
});
