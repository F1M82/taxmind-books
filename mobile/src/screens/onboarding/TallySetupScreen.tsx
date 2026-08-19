import React, { useCallback, useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import {
  ConnectorStatus,
  TallyCompanyDiscovery,
  getConnectorStatus,
  getTallyCompanies,
} from "../../api/connector";
import { useActiveCompany } from "../../context/CompanyContext";

export default function TallySetupScreen({
  onCreateCompany,
  onReviewExistingCompany,
}: {
  onCreateCompany?: (discoveryId: string) => void;
  onReviewExistingCompany?: (discoveryId: string) => void;
}): React.ReactElement {
  const { activeCompanyId, activeCompanyVersion, setActive } = useActiveCompany();
  const [connector, setConnector] = useState<ConnectorStatus | null>(null);
  const [companies, setCompanies] = useState<TallyCompanyDiscovery[] | null>(null);
  const [selected, setSelected] = useState<TallyCompanyDiscovery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (refresh = false) => {
    const requestedCompanyId = activeCompanyId;
    setError(null);
    setCompanies(null);
    setSelected(null);
    setRefreshing(true);
    try {
      const status = await getConnectorStatus();
      if (activeCompanyId !== requestedCompanyId) return;
      setConnector(status);
      if (!status.connector_id) {
        throw new Error("Connector identity is missing from status.");
      }
      const discovered = await getTallyCompanies(status.connector_id, refresh);
      if (activeCompanyId !== requestedCompanyId) return;
      setCompanies(discovered.companies);
    } catch {
      setError("Could not load discovered Tally companies. Check the connector and try again.");
    } finally {
      setRefreshing(false);
    }
  }, [activeCompanyId, activeCompanyVersion]);

  useEffect(() => {
    if (activeCompanyId === null) {
      setConnector(null);
      setCompanies(null);
      setSelected(null);
      return;
    }
    void load();
  }, [activeCompanyId, load]);

  const selectCompany = (company: TallyCompanyDiscovery): void => {
    if (company.mapped_to_backend_company_id !== null) {
      void setActive(company.mapped_to_backend_company_id);
      return;
    }
    setSelected(company);
  };

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} />}>
      <Text style={styles.title}>Connect Tally</Text>
      <Text style={styles.subtitle}>Choose a company discovered by your connected Tally Connector.</Text>
      {connector && <Text accessibilityLabel="connector-status" style={styles.status}>{connector.connected ? "Connector connected" : "Connector not connected"}</Text>}
      {error && <Text style={styles.error}>{error}</Text>}
      {companies && companies.length === 0 && <Text>No Tally companies discovered yet.</Text>}
      {companies?.map((company) => {
        const isMapped = company.mapped_to_backend_company_id === activeCompanyId;
        const mappedElsewhere = company.mapped_to_backend_company_id !== null && !isMapped;
        return (
           <Pressable key={company.discovery_id} accessibilityRole="button" accessibilityLabel={`tally-company-${company.discovery_id}`} onPress={() => selectCompany(company)} style={[styles.card, selected?.discovery_id === company.discovery_id && styles.selected]}>
            <Text style={styles.name}>{company.tally_company_name}</Text>
            <Text style={styles.meta}>{company.gstin ?? "No GSTIN"}</Text>
            <Text style={isMapped ? styles.mapped : mappedElsewhere ? styles.unavailable : styles.unmapped}>
              {isMapped ? "Mapped to this company" : mappedElsewhere ? "Mapped to another company" : "Unmapped"}
            </Text>
          </Pressable>
        );
      })}
       {selected && <View style={styles.confirmBox}>
         <Text>Review mapping for {selected.tally_company_name}</Text>
         <Text style={styles.subtitle}>Choose an authorized company before mapping. This will not map the current company automatically.</Text>
         {onReviewExistingCompany && <Pressable accessibilityRole="button" accessibilityLabel="review-existing-company" onPress={() => onReviewExistingCompany(selected.discovery_id)} style={styles.button}><Text style={styles.buttonText}>Choose an existing company</Text></Pressable>}
         {onCreateCompany && <Pressable accessibilityRole="button" accessibilityLabel="create-company-for-tally" onPress={() => onCreateCompany(selected.discovery_id)} style={styles.secondaryButton}><Text style={styles.secondaryButtonText}>Create a new company</Text></Pressable>}
       </View>}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12 }, title: { fontSize: 22, fontWeight: "700" }, subtitle: { color: "#555" }, status: { fontWeight: "600" }, error: { color: "#c0392b" }, card: { padding: 14, borderWidth: 1, borderColor: "#ddd", borderRadius: 8, gap: 4 }, selected: { borderColor: "#2c3e50", backgroundColor: "#f3f6f8" }, name: { fontSize: 16, fontWeight: "600" }, meta: { color: "#666" }, mapped: { color: "#1e7e34", fontWeight: "600" }, unmapped: { color: "#8a6d3b" }, unavailable: { color: "#777" }, confirmBox: { padding: 14, gap: 10, borderRadius: 8, backgroundColor: "#f6f8fa" }, button: { padding: 14, borderRadius: 8, alignItems: "center", backgroundColor: "#2c3e50" }, buttonText: { color: "#fff", fontWeight: "600" }, secondaryButton: { padding: 14, borderRadius: 8, alignItems: "center", borderWidth: 1, borderColor: "#2c3e50" }, secondaryButtonText: { color: "#2c3e50", fontWeight: "600" },
});
