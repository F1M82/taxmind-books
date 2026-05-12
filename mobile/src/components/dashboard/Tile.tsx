import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

/**
 * Shared shell for a dashboard tile: a Pressable card with a label,
 * a primary value (or children for richer content), and an optional
 * subtitle. Used by ConnectorTile, MetricsTile, OutstandingTile,
 * and GstTile.
 */
export default function Tile({
  label,
  primary,
  subtitle,
  onPress,
  accessibilityLabel,
  children,
  tone,
}: {
  label: string;
  primary?: string;
  subtitle?: string;
  onPress?: () => void;
  accessibilityLabel?: string;
  children?: React.ReactNode;
  /** Visual accent for the tile. */
  tone?: "default" | "good" | "warn" | "bad";
}): React.ReactElement {
  const toneStyle =
    tone === "good"
      ? styles.toneGood
      : tone === "warn"
      ? styles.toneWarn
      : tone === "bad"
      ? styles.toneBad
      : styles.toneDefault;

  const body = (
    <View
      accessibilityLabel={onPress === undefined ? accessibilityLabel : undefined}
      style={[styles.tile, toneStyle]}
    >
      <Text style={styles.label}>{label}</Text>
      {primary !== undefined && <Text style={styles.primary}>{primary}</Text>}
      {children}
      {subtitle !== undefined && (
        <Text style={styles.subtitle}>{subtitle}</Text>
      )}
    </View>
  );

  if (onPress === undefined) {
    return body;
  }
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      onPress={onPress}
      style={({ pressed }) => [
        styles.pressable,
        pressed && { opacity: 0.85 },
      ]}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pressable: { flex: 1 },
  tile: {
    flex: 1,
    padding: 14,
    borderRadius: 10,
    borderWidth: 1,
    gap: 4,
    minHeight: 96,
  },
  toneDefault: { backgroundColor: "#fff", borderColor: "#e0e0e0" },
  toneGood: { backgroundColor: "#eafaf1", borderColor: "#27ae60" },
  toneWarn: { backgroundColor: "#fef5e7", borderColor: "#f39c12" },
  toneBad: { backgroundColor: "#fdecea", borderColor: "#c0392b" },
  label: { fontSize: 12, color: "#666", letterSpacing: 0.5 },
  primary: { fontSize: 20, fontWeight: "700", color: "#222" },
  subtitle: { fontSize: 12, color: "#666" },
});
