import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import {
  ApiError,
} from "../../api/client";
import {
  CompanyMappingStatus,
  ConnectorStatus,
  confirmCompanyMapping,
  getCompanyMapping,
  getConnectorStatus,
} from "../../api/connector";

/**
 * Tally Setup screen (P3.7 Phase 7C mobile).
 *
 * Replaces the inert onboarding "Install Tally Connector" row with a
 * real workflow surface:
 *
 *   connector state      ← GET /connector/status
 *   mapping state        ← GET /connector/company-mapping
 *   operator confirmation→ POST /connector/company-mapping/confirm
 *
 * The Tally company GUID is explicitly supplied by the operator and is
 * the authoritative identity — never derived from a name. No ledger
 * sync is triggered here; mapping confirmation only populates
 * `Company.tally_master_id` on the backend.
 */
export default function TallySetupScreen(): React.ReactElement {
  const [connector, setConnector] = useState<ConnectorStatus | null>(null);
  const [mapping, setMapping] = useState<CompanyMappingStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [tallyName, setTallyName] = useState("");
  const [tallyGuid, setTallyGuid] = useState("");
  const [entering, setEntering] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [mappedName, setMappedName] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    setRefreshing(true);
    try {
      const [conn, map] = await Promise.all([
        getConnectorStatus(),
        getCompanyMapping(),
      ]);
      setConnector(conn);
      setMapping(map);
      if (!map.mapped) {
        setEntering(true);
      }
    } catch {
      setLoadError("Could not load Tally Setup. Check the backend and try again.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onConfirm = async (): Promise<void> => {
    const guid = tallyGuid.trim();
    if (guid.length === 0 || confirming) {
      return;
    }
    setConfirmError(null);
    setConfirming(true);
    try {
      const resp = await confirmCompanyMapping({
        tally_company_guid: guid,
        tally_company_name: tallyName.trim() === "" ? null : tallyName.trim(),
      });
      // Mapped state is backend-confirmed only — never fabricated locally.
      setMapping({
        company_id: resp.company_id,
        tally_master_id: resp.tally_master_id,
        mapped: true,
      });
      const fallback = tallyName.trim();
      setMappedName(resp.tally_company_name ?? (fallback === "" ? null : fallback));
      setEntering(false);
    } catch (exc) {
      setConfirmError(messageFor(exc));
    } finally {
      setConfirming(false);
    }
  };

  const connectorLine = connectorStatusText(connector);

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      accessibilityLabel="tally-setup"
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={load} />
      }
    >
      <Text style={styles.title}>Tally Setup</Text>

      {connector === null && mapping === null && loadError === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}
      {loadError !== null && <Text style={styles.error}>{loadError}</Text>}

      {connector !== null && (
        <View
          style={styles.card}
          accessibilityLabel="connector-status"
        >
          <Text style={styles.cardLabel}>TALLY CONNECTOR</Text>
          <Text style={styles.cardValue}>{connectorLine}</Text>
        </View>
      )}

      {connector !== null && connector.connected === false && (
        <Text style={styles.hint}>
          Start the Tally Connector on your PC and pair it before proceeding.
        </Text>
      )}

      {mapping !== null && (
        <View style={styles.card} accessibilityLabel="mapping-status">
          <Text style={styles.cardLabel}>TALLY COMPANY MAPPING</Text>
          {mapping.mapped ? (
            <>
              <Text style={styles.mappedRow}>
                ✓ Mapped to {mappedName ?? "a Tally company"}
              </Text>
              {mapping.tally_master_id !== null && (
                <Text style={styles.guid}>{mapping.tally_master_id}</Text>
              )}
              {!entering && (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="change-mapping"
                  onPress={() => {
                    setTallyGuid(mapping.tally_master_id ?? "");
                    setEntering(true);
                  }}
                  style={({ pressed }) => [
                    styles.link,
                    pressed && { opacity: 0.8 },
                  ]}
                >
                  <Text style={styles.linkText}>Change mapping</Text>
                </Pressable>
              )}
            </>
          ) : (
            <Text style={styles.cardValue}>Not mapped to a Tally company yet</Text>
          )}
        </View>
      )}

      {onConfirmRelevant(mapping, entering) && (
        <View style={styles.card}>
          <Text style={styles.cardLabel}>CONNECT TALLY COMPANY</Text>
          <TextInput
            accessibilityLabel="tally-name"
            placeholder="Tally company name (display only)"
            value={tallyName}
            onChangeText={setTallyName}
            style={styles.input}
          />
          <TextInput
            accessibilityLabel="tally-guid"
            placeholder="Tally company GUID"
            autoCapitalize="none"
            autoCorrect={false}
            value={tallyGuid}
            onChangeText={setTallyGuid}
            style={styles.input}
          />

          <View style={styles.review}>
            <Text style={styles.reviewLabel}>Tally Company Found</Text>
            <Text style={styles.reviewName}>
              {tallyName.trim() === "" ? "Tally company" : tallyName.trim()}
            </Text>
            <Text style={styles.reviewGuidLabel}>Tally Company GUID</Text>
            <Text style={styles.reviewGuid}>
              {tallyGuid.trim() === "" ? "—" : tallyGuid.trim()}
            </Text>
          </View>

          {confirmError !== null && (
            <Text style={styles.error} accessibilityLabel="mapping-error">
              {confirmError}
            </Text>
          )}

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="confirm-mapping"
            onPress={() => void onConfirm()}
            disabled={tallyGuid.trim().length === 0 || confirming}
            style={({ pressed }) => [
              styles.button,
              (tallyGuid.trim().length === 0 || confirming) &&
                styles.buttonDisabled,
              pressed && { opacity: 0.85 },
            ]}
          >
            {confirming ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.buttonText}>Confirm Mapping</Text>
            )}
          </Pressable>
        </View>
      )}
    </ScrollView>
  );
}

/** Only show the connect form when there is nothing to map yet, or the
 *  operator explicitly opened re-mapping. */
function onConfirmRelevant(
  mapping: CompanyMappingStatus | null,
  entering: boolean,
): boolean {
  if (mapping === null) {
    return false;
  }
  return !mapping.mapped || entering;
}

function connectorStatusText(connector: ConnectorStatus | null): string {
  if (connector === null) {
    return "Checking…";
  }
  if (!connector.connected) {
    return "Not connected";
  }
  if (connector.tally_running === false) {
    return "Connected — Tally not running";
  }
  return connector.tally_running === true
    ? "Connected — Tally is running"
    : "Connected";
}

function messageFor(exc: unknown): string {
  if (exc instanceof ApiError) {
    if (exc.code === "company_mapping_conflict") {
      return "Mapping conflict: that Tally company GUID is already in use or the company is bound differently.";
    }
    if (exc.status === 401 || exc.status === 403) {
      return "Authorization failed. You need owner access to map this company.";
    }
    if (exc.status === 0 || typeof exc.message !== "string" || exc.message === "") {
      return "Could not reach the backend. Try again.";
    }
    return exc.message;
  }
  return "Could not confirm mapping. Try again.";
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 },
  center: { padding: 24, alignItems: "center" },
  title: { fontSize: 20, fontWeight: "700" },
  card: {
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 8,
  },
  cardLabel: { fontSize: 12, fontWeight: "700", color: "#666" },
  cardValue: { fontSize: 15, fontWeight: "600", color: "#222" },
  mappedRow: { fontSize: 15, fontWeight: "600", color: "#1e7e34" },
  guid: { fontSize: 13, color: "#444" },
  hint: { fontSize: 13, color: "#8a6d3b" },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  review: {
    padding: 12,
    backgroundColor: "#f6f8fa",
    borderWidth: 1,
    borderColor: "#dfe3e8",
    borderRadius: 8,
    gap: 2,
  },
  reviewLabel: { fontSize: 12, fontWeight: "700", color: "#2c3e50" },
  reviewName: { fontSize: 16, fontWeight: "600", color: "#222" },
  reviewGuidLabel: { fontSize: 12, color: "#666", marginTop: 6 },
  reviewGuid: { fontSize: 14, color: "#2c3e50" },
  button: {
    backgroundColor: "#2c3e50",
    padding: 14,
    borderRadius: 8,
    alignItems: "center",
  },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  buttonDisabled: { backgroundColor: "#95a5a6" },
  link: { alignSelf: "flex-start", marginTop: 2 },
  linkText: { color: "#2c3e50", fontWeight: "600", fontSize: 14 },
  error: { color: "#c0392b", fontSize: 14 },
});