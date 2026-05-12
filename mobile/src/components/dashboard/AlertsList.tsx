import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { DashboardAlert } from "../../api/dashboard";

/**
 * Vertical stack of alert cards. Each alert is tappable when the
 * dashboard passes an onPress handler keyed off `alert.kind` —
 * e.g. `pending_approvals` navigates to the vouchers list.
 */
export default function AlertsList({
  alerts,
  onPressAlert,
}: {
  alerts: DashboardAlert[];
  onPressAlert?: (alert: DashboardAlert) => void;
}): React.ReactElement | null {
  if (alerts.length === 0) {
    return null;
  }
  return (
    <View accessibilityLabel="alerts-list" style={styles.list}>
      {alerts.map((a, idx) => (
        <AlertCard
          key={`${a.kind}-${idx}`}
          alert={a}
          onPress={
            onPressAlert === undefined ? undefined : () => onPressAlert(a)
          }
        />
      ))}
    </View>
  );
}

function AlertCard({
  alert,
  onPress,
}: {
  alert: DashboardAlert;
  onPress?: () => void;
}): React.ReactElement {
  const toneStyle =
    alert.severity === "critical"
      ? styles.critical
      : alert.severity === "warning"
      ? styles.warning
      : styles.info;
  const body = (
    <View
      style={[styles.card, toneStyle]}
      accessibilityLabel={`alert-${alert.kind}`}
    >
      <Text style={styles.severity}>{alert.severity.toUpperCase()}</Text>
      <Text style={styles.message}>{alert.message}</Text>
    </View>
  );
  if (onPress === undefined) {
    return body;
  }
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [pressed && { opacity: 0.85 }]}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  list: { gap: 8 },
  card: { padding: 10, borderRadius: 8, gap: 2 },
  info: { backgroundColor: "#eaf2f8", borderWidth: 1, borderColor: "#85c1e9" },
  warning: {
    backgroundColor: "#fef5e7",
    borderWidth: 1,
    borderColor: "#f39c12",
  },
  critical: {
    backgroundColor: "#fdecea",
    borderWidth: 1,
    borderColor: "#c0392b",
  },
  severity: { fontSize: 10, color: "#444", letterSpacing: 1 },
  message: { fontSize: 14, color: "#222" },
});
