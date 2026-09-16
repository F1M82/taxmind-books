/**
 * Owner-only: issue a one-time connector enrollment code for a new
 * computer. Completes the self-service half of enrollment started by
 * connector/connector/enrollment.py (2026-09-16) -- that piece made
 * *exchanging* a code interactive (no curl needed); this screen makes
 * *issuing* one self-service too, closing the remaining gap recorded
 * in the `connector_self_service_enrollment_gap` memory.
 *
 * This screen only ever calls POST /connector/enrollment-codes. It
 * never talks to a connector directly -- the code is handed to a
 * human, who types it into the connector's own first-run prompt.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { EnrollmentCode, issueEnrollmentCode } from "../../api/connector";
import { useAuth } from "../../context/AuthContext";
import { useActiveCompany } from "../../context/CompanyContext";

function secondsRemaining(expiresAt: string): number {
  const ms = new Date(expiresAt).getTime() - Date.now();
  return Math.max(0, Math.floor(ms / 1000));
}

function formatMMSS(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function AddDeviceScreen(): React.ReactElement {
  const { user } = useAuth();
  const { activeCompanyId } = useActiveCompany();
  const [issuing, setIssuing] = useState(false);
  const [enrollment, setEnrollment] = useState<EnrollmentCode | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const activeCompany =
    activeCompanyId === null
      ? null
      : (user?.companies.find((c) => c.id === activeCompanyId) ?? null);
  const isOwner = activeCompany?.role === "owner";

  const onIssue = useCallback(async () => {
    setError(null);
    setIssuing(true);
    try {
      const resp = await issueEnrollmentCode();
      setEnrollment(resp);
      setRemaining(secondsRemaining(resp.expires_at));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not issue a code.");
    } finally {
      setIssuing(false);
    }
  }, []);

  // Live countdown -- the code is single-use and expires in 15 minutes;
  // showing the clock ticking down makes that concrete instead of a
  // static timestamp the reader has to do math on.
  useEffect(() => {
    if (enrollment === null) return;
    const id = setInterval(() => {
      setRemaining(secondsRemaining(enrollment.expires_at));
    }, 1000);
    return () => clearInterval(id);
  }, [enrollment]);

  const expired = enrollment !== null && remaining <= 0;

  if (!isOwner) {
    return (
      <View style={styles.center}>
        <Text style={styles.note}>Only an owner can add a new device.</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.intro}>
        On the new computer, run TaxMindBooksConnector.exe. If it has no
        saved token yet, it will prompt for an enrollment code — type the
        one below.
      </Text>

      {enrollment === null && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="issue-enrollment-code"
          disabled={issuing}
          onPress={onIssue}
          style={({ pressed }) => [
            styles.button,
            issuing && styles.buttonDisabled,
            pressed && { opacity: 0.85 },
          ]}
        >
          {issuing ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Generate a code</Text>
          )}
        </Pressable>
      )}

      {error !== null && <Text style={styles.error}>{error}</Text>}

      {enrollment !== null && !expired && (
        <View style={styles.codeBox}>
          <Text
            selectable
            accessibilityLabel="enrollment-code"
            style={styles.code}
          >
            {enrollment.code}
          </Text>
          <Text style={styles.countdown}>
            Expires in {formatMMSS(remaining)}
          </Text>
          <Text style={styles.hint}>
            Long-press the code to copy it. Single-use — once the new
            computer's connector uses it, come back here for the next one.
          </Text>
        </View>
      )}

      {expired && (
        <View style={styles.codeBox}>
          <Text style={styles.expiredText}>
            That code expired before it was used.
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="issue-another-code"
            onPress={() => {
              setEnrollment(null);
              void onIssue();
            }}
            style={({ pressed }) => [styles.button, pressed && { opacity: 0.85 }]}
          >
            <Text style={styles.buttonText}>Generate another</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, gap: 16 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  intro: { fontSize: 14, color: "#444", lineHeight: 20 },
  button: {
    padding: 14,
    borderRadius: 8,
    backgroundColor: "#2c3e50",
    alignItems: "center",
  },
  buttonDisabled: { backgroundColor: "#95a5a6" },
  buttonText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  codeBox: {
    borderWidth: 1,
    borderColor: "#e0e0e0",
    borderRadius: 8,
    padding: 16,
    gap: 8,
    alignItems: "center",
  },
  code: {
    fontSize: 18,
    fontFamily: "monospace",
    fontWeight: "700",
    letterSpacing: 1,
    textAlign: "center",
  },
  countdown: { fontSize: 13, color: "#c0392b", fontWeight: "600" },
  hint: { fontSize: 12, color: "#666", textAlign: "center" },
  expiredText: { fontSize: 14, color: "#c0392b", textAlign: "center" },
  note: { color: "#666", fontSize: 14, textAlign: "center" },
  error: { color: "#c0392b" },
});
