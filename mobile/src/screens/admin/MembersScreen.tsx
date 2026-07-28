import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiError, getActiveCompanyId } from "../../api/client";
import {
  MEMBER_ROLES,
  Member,
  MemberRole,
  addMember,
  listMembers,
  removeMember,
  setMemberRole,
} from "../../api/companies";
import { useAuth } from "../../context/AuthContext";

function roleOf(
  user: ReturnType<typeof useAuth>["user"],
  companyId: string | null,
): string | null {
  if (user === null || companyId === null) return null;
  return user.companies.find((c) => c.id === companyId)?.role ?? null;
}

export default function MembersScreen(): React.ReactElement {
  const { user } = useAuth();
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState<MemberRole>("accountant");
  const [busy, setBusy] = useState(false);

  const myRole = roleOf(user, companyId);
  const isOwner = myRole === "owner";

  const load = useCallback(async () => {
    setError(null);
    setRefreshing(true);
    try {
      const cid = await getActiveCompanyId();
      setCompanyId(cid);
      if (cid === null) {
        setError("No active company selected.");
        setMembers([]);
        return;
      }
      const resp = await listMembers(cid);
      setMembers(resp.items);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not load members.",
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onAdd = useCallback(async () => {
    if (companyId === null || email.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      await addMember(companyId, email.trim(), newRole);
      setEmail("");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add member.");
    } finally {
      setBusy(false);
    }
  }, [companyId, email, newRole, load]);

  const onChangeRole = useCallback(
    (m: Member) => {
      if (companyId === null) return;
      const options = MEMBER_ROLES.filter((r) => r !== m.role);
      Alert.alert(
        `Change role — ${m.user_email}`,
        `Currently ${m.role}`,
        [
          ...options.map((r) => ({
            text: r,
            onPress: () => {
              void (async () => {
                try {
                  await setMemberRole(companyId, m.user_id, r);
                  await load();
                } catch (e) {
                  Alert.alert(
                    "Could not change role",
                    e instanceof ApiError ? e.message : "Unknown error",
                  );
                }
              })();
            },
          })),
          { text: "Cancel", style: "cancel" as const },
        ],
      );
    },
    [companyId, load],
  );

  const onRemove = useCallback(
    (m: Member) => {
      if (companyId === null) return;
      Alert.alert(
        "Remove member",
        `Remove ${m.user_email} from this company?`,
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Remove",
            style: "destructive",
            onPress: () => {
              void (async () => {
                try {
                  await removeMember(companyId, m.user_id);
                  await load();
                } catch (e) {
                  Alert.alert(
                    "Could not remove",
                    e instanceof ApiError ? e.message : "Unknown error",
                  );
                }
              })();
            },
          },
        ],
      );
    },
    [companyId, load],
  );

  return (
    <View style={styles.container}>
      {isOwner && (
        <View style={styles.addBox}>
          <Text style={styles.addTitle}>Add member</Text>
          <TextInput
            accessibilityLabel="member-email"
            style={styles.input}
            placeholder="user@example.com"
            autoCapitalize="none"
            keyboardType="email-address"
            value={email}
            onChangeText={setEmail}
          />
          <View style={styles.roleRow}>
            {MEMBER_ROLES.map((r) => (
              <Pressable
                key={r}
                accessibilityLabel={`role-${r}`}
                onPress={() => setNewRole(r)}
                style={[
                  styles.roleChip,
                  newRole === r && styles.roleChipActive,
                ]}
              >
                <Text
                  style={[
                    styles.roleChipText,
                    newRole === r && styles.roleChipTextActive,
                  ]}
                >
                  {r}
                </Text>
              </Pressable>
            ))}
          </View>
          <Pressable
            accessibilityLabel="add-member"
            disabled={busy || email.trim() === ""}
            onPress={onAdd}
            style={({ pressed }) => [
              styles.addButton,
              (busy || email.trim() === "") && styles.addButtonDisabled,
              pressed && { opacity: 0.85 },
            ]}
          >
            <Text style={styles.addButtonText}>
              {busy ? "Adding…" : "Add member"}
            </Text>
          </Pressable>
        </View>
      )}
      {members === null && error === null && (
        <View style={styles.center}>
          <ActivityIndicator />
        </View>
      )}
      <ScrollView
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={load} />
        }
      >
        {error !== null && <Text style={styles.error}>{error}</Text>}
        {members !== null && members.length === 0 && error === null && (
          <Text style={styles.empty}>No members.</Text>
        )}
        {members !== null &&
          members.map((m) => (
            <View key={m.id} style={styles.row}>
              <View style={styles.rowMain}>
                <Text style={styles.title}>{m.user_email}</Text>
                <Text style={styles.roleBadge}>{m.role}</Text>
              </View>
              {isOwner && (
                <View style={styles.actions}>
                  <Pressable
                    accessibilityLabel={`change-role-${m.user_email}`}
                    onPress={() => onChangeRole(m)}
                    style={styles.actionBtn}
                  >
                    <Text style={styles.actionText}>Role</Text>
                  </Pressable>
                  <Pressable
                    accessibilityLabel={`remove-${m.user_email}`}
                    onPress={() => onRemove(m)}
                    style={[styles.actionBtn, styles.removeBtn]}
                  >
                    <Text style={[styles.actionText, styles.removeText]}>
                      Remove
                    </Text>
                  </Pressable>
                </View>
              )}
            </View>
          ))}
        {!isOwner && members !== null && members.length > 0 && (
          <Text style={styles.note}>
            Only an owner can add, change roles, or remove members.
          </Text>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
  addBox: {
    margin: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    gap: 10,
  },
  addTitle: { fontSize: 16, fontWeight: "600" },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 15,
  },
  roleRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  roleChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#ccc",
  },
  roleChipActive: { backgroundColor: "#2c3e50", borderColor: "#2c3e50" },
  roleChipText: { color: "#333", fontSize: 13 },
  roleChipTextActive: { color: "#fff", fontWeight: "600" },
  addButton: {
    padding: 12,
    borderRadius: 8,
    backgroundColor: "#2c3e50",
    alignItems: "center",
  },
  addButtonDisabled: { backgroundColor: "#95a5a6" },
  addButtonText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  list: { paddingHorizontal: 16, paddingBottom: 24, gap: 8 },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 14,
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
  },
  rowMain: { gap: 4, flexShrink: 1 },
  title: { fontSize: 15, fontWeight: "600" },
  roleBadge: {
    fontSize: 12,
    color: "#2c3e50",
    backgroundColor: "#ecf0f1",
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
    overflow: "hidden",
  },
  actions: { flexDirection: "row", gap: 8 },
  actionBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: "#2c3e50",
  },
  actionText: { color: "#2c3e50", fontSize: 13, fontWeight: "600" },
  removeBtn: { borderColor: "#c0392b" },
  removeText: { color: "#c0392b" },
  empty: { textAlign: "center", color: "#666", padding: 24 },
  note: { color: "#666", fontSize: 12, padding: 12, textAlign: "center" },
  error: { color: "#c0392b", padding: 16 },
});
