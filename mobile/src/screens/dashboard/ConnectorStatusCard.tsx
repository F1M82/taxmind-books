import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ConnectorStatus, getConnectorStatus } from "../../api/connector";

export default function ConnectorStatusCard(): React.ReactElement {
  const [status, setStatus] = useState<ConnectorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await getConnectorStatus());
    } catch {
      setError("Could not load connector status.");
    }
  }, []);

  useEffect(() => {
    void load();
    const handle = setInterval(load, 30_000);
    return () => clearInterval(handle);
  }, [load]);

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Tally Connector</Text>
      {error !== null && <Text style={styles.error}>{error}</Text>}
      {error === null && status === null && <ActivityIndicator />}
      {status !== null && (
        <View style={styles.body}>
          <View style={styles.row}>
            <View
              accessibilityLabel="status-dot"
              style={[
                styles.dot,
                status.connected ? styles.dotOn : styles.dotOff,
              ]}
            />
            <Text style={styles.statusText}>
              {status.connected ? "Connected" : "Disconnected"}
            </Text>
          </View>
          {status.connected && (
            <>
              <Text style={styles.meta}>
                Tally:{" "}
                {status.tally_running === true ? "running" : "not detected"}
                {status.tally_version !== null
                  ? ` (v${status.tally_version})`
                  : ""}
              </Text>
              {status.connector_version !== null && (
                <Text style={styles.meta}>
                  Connector v{status.connector_version}
                </Text>
              )}
              {status.queued_outbound_count !== null &&
                status.queued_outbound_count > 0 && (
                  <Text style={styles.meta}>
                    Queued: {status.queued_outbound_count}
                  </Text>
                )}
            </>
          )}
          {!status.connected && status.last_seen_at !== null && (
            <Text style={styles.meta}>
              Last seen: {new Date(status.last_seen_at).toLocaleString()}
            </Text>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: 16,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 6,
  },
  title: { fontSize: 18, fontWeight: "600" },
  body: { gap: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  dotOn: { backgroundColor: "#27ae60" },
  dotOff: { backgroundColor: "#95a5a6" },
  statusText: { fontSize: 16 },
  meta: { fontSize: 14, color: "#666" },
  error: { color: "#c0392b" },
});
