import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { OnboardingChecklistResponse } from "../../api/onboarding";
import Tile from "./Tile";

/**
 * Compact dashboard tile that surfaces onboarding progress.
 *
 * The Phase-0 `first_invoice_extracted` item is permanently
 * incomplete (the feature lands in Phase 1+), so a healthy
 * Phase-0 account caps at 4 of 5. We pass the raw counts through
 * and let the user open the full screen for the per-item view.
 */
export default function OnboardingTile({
  data,
  onPress,
}: {
  data: OnboardingChecklistResponse;
  onPress: () => void;
}): React.ReactElement {
  const tone =
    data.completed_count >= data.total_count - 1 ? "good" : "warn";

  return (
    <Tile
      label="ONBOARDING"
      onPress={onPress}
      accessibilityLabel="tile-onboarding"
      tone={tone}
    >
      <Text style={styles.primary}>
        {data.completed_count} of {data.total_count} complete
      </Text>
      <View style={styles.barTrack}>
        <View
          style={[
            styles.barFill,
            {
              width: `${Math.min(
                100,
                Math.round((data.completed_count / data.total_count) * 100),
              )}%`,
            },
          ]}
        />
      </View>
      <Text style={styles.hint}>Tap to see what's left</Text>
    </Tile>
  );
}

const styles = StyleSheet.create({
  primary: { fontSize: 18, fontWeight: "700", color: "#222" },
  barTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: "#e0e0e0",
    overflow: "hidden",
    marginTop: 4,
  },
  barFill: { height: "100%", backgroundColor: "#27ae60" },
  hint: { fontSize: 12, color: "#666", marginTop: 2 },
});
