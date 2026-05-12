import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { DashboardOutstanding } from "../../api/dashboard";
import { formatINR } from "../../utils/money";
import Tile from "./Tile";

/**
 * Two-column tile showing receivables and payables totals. Tap
 * navigates to the Outstanding screen; the screen's tab defaults
 * to receivables, which matches the more common starting question.
 */
export default function OutstandingTile({
  outstanding,
  onPress,
}: {
  outstanding: DashboardOutstanding;
  onPress?: () => void;
}): React.ReactElement {
  return (
    <Tile
      label="OUTSTANDING"
      onPress={onPress}
      accessibilityLabel="tile-outstanding"
    >
      <View style={styles.row}>
        <View style={styles.col}>
          <Text style={styles.colLabel}>Receivables</Text>
          <Text style={styles.colReceive}>
            {formatINR(outstanding.receivables_total)}
          </Text>
        </View>
        <View style={styles.col}>
          <Text style={styles.colLabel}>Payables</Text>
          <Text style={styles.colPay}>
            {formatINR(outstanding.payables_total)}
          </Text>
        </View>
      </View>
    </Tile>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 12 },
  col: { flex: 1 },
  colLabel: { fontSize: 11, color: "#666" },
  colReceive: { fontSize: 16, fontWeight: "700", color: "#27ae60" },
  colPay: { fontSize: 16, fontWeight: "700", color: "#c0392b" },
});
