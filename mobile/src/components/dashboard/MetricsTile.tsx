import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { formatINR } from "../../utils/money";
import Tile from "./Tile";

/**
 * Renders a today- or this-month metrics tile: vouchers count,
 * cash-in and cash-out amounts side by side. Tappable so the
 * dashboard can navigate to the vouchers list.
 */
export default function MetricsTile({
  label,
  vouchersCreated,
  pendingApproval,
  cashIn,
  cashOut,
  onPress,
  accessibilityLabel,
}: {
  label: string;
  vouchersCreated: number;
  pendingApproval: number;
  cashIn: string;
  cashOut: string;
  onPress?: () => void;
  accessibilityLabel?: string;
}): React.ReactElement {
  const subtitle =
    pendingApproval > 0
      ? `${pendingApproval} pending approval`
      : "All approved";
  return (
    <Tile
      label={label}
      onPress={onPress}
      accessibilityLabel={accessibilityLabel}
      subtitle={subtitle}
    >
      <Text style={styles.primary}>{vouchersCreated} vouchers</Text>
      <View style={styles.cashRow}>
        <View style={styles.cashCol}>
          <Text style={styles.cashLabel}>Cash in</Text>
          <Text style={styles.cashIn}>{formatINR(cashIn)}</Text>
        </View>
        <View style={styles.cashCol}>
          <Text style={styles.cashLabel}>Cash out</Text>
          <Text style={styles.cashOut}>{formatINR(cashOut)}</Text>
        </View>
      </View>
    </Tile>
  );
}

const styles = StyleSheet.create({
  primary: { fontSize: 18, fontWeight: "700", color: "#222" },
  cashRow: { flexDirection: "row", gap: 12, marginTop: 2 },
  cashCol: { flex: 1 },
  cashLabel: { fontSize: 11, color: "#666" },
  cashIn: { fontSize: 14, fontWeight: "600", color: "#27ae60" },
  cashOut: { fontSize: 14, fontWeight: "600", color: "#c0392b" },
});
