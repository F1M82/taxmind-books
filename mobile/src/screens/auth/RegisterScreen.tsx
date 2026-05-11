import React, { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { useAuth } from "../../context/AuthContext";

export default function RegisterScreen({
  onSuccess,
  onBackToLogin,
}: {
  onSuccess: () => void;
  onBackToLogin: () => void;
}): React.ReactElement {
  const { signUp } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [isCa, setIsCa] = useState(false);
  const [firmName, setFirmName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (): Promise<void> => {
    setError(null);
    setSubmitting(true);
    try {
      await signUp({
        email: email.trim(),
        password,
        full_name: fullName.trim(),
        phone: phone.trim() === "" ? null : phone.trim(),
        is_ca: isCa,
        firm_name: isCa && firmName.trim() !== "" ? firmName.trim() : null,
      });
      onSuccess();
    } catch (exc) {
      if (
        exc instanceof ApiError &&
        exc.code === "email_already_registered"
      ) {
        setError("That email is already registered.");
      } else if (exc instanceof ApiError && exc.code === "validation_error") {
        setError("Please check your inputs and try again.");
      } else {
        setError("Could not create your account. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Create your account</Text>
      <TextInput
        accessibilityLabel="full-name"
        placeholder="Full name"
        value={fullName}
        onChangeText={setFullName}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="email"
        placeholder="Email"
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="phone"
        placeholder="Phone (optional, +91…)"
        keyboardType="phone-pad"
        value={phone}
        onChangeText={setPhone}
        style={styles.input}
      />
      <TextInput
        accessibilityLabel="password"
        placeholder="Password (min 12 chars)"
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        style={styles.input}
      />
      <View style={styles.row}>
        <Text style={styles.label}>I am a CA</Text>
        <Switch
          accessibilityLabel="is-ca"
          value={isCa}
          onValueChange={setIsCa}
        />
      </View>
      {isCa && (
        <TextInput
          accessibilityLabel="firm-name"
          placeholder="Firm name"
          value={firmName}
          onChangeText={setFirmName}
          style={styles.input}
        />
      )}
      {error !== null && <Text style={styles.error}>{error}</Text>}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="register"
        onPress={onSubmit}
        disabled={
          submitting ||
          email.length === 0 ||
          password.length < 12 ||
          fullName.length === 0
        }
        style={({ pressed }) => [
          styles.button,
          (submitting ||
            email.length === 0 ||
            password.length < 12 ||
            fullName.length === 0) &&
            styles.buttonDisabled,
          pressed && styles.buttonPressed,
        ]}
      >
        {submitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Create account</Text>
        )}
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="back-to-login"
        onPress={onBackToLogin}
      >
        <Text style={styles.link}>Back to sign-in</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: 24,
    gap: 10,
  },
  title: {
    fontSize: 24,
    fontWeight: "600",
    marginBottom: 16,
  },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 4,
  },
  label: { fontSize: 16 },
  error: { color: "#c0392b" },
  button: {
    backgroundColor: "#2c3e50",
    padding: 14,
    borderRadius: 8,
    alignItems: "center",
  },
  buttonDisabled: { backgroundColor: "#95a5a6" },
  buttonPressed: { opacity: 0.85 },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  link: { color: "#2980b9", textAlign: "center", marginTop: 8 },
});
