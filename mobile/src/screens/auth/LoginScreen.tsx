import React, { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiError } from "../../api/client";
import { useAuth } from "../../context/AuthContext";

export default function LoginScreen({
  onNavigateToRegister,
}: {
  onNavigateToRegister: () => void;
}): React.ReactElement {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (): Promise<void> => {
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email.trim(), password);
    } catch (exc) {
      if (exc instanceof ApiError && exc.code === "invalid_credentials") {
        setError("Invalid email or password.");
      } else if (exc instanceof ApiError && exc.code === "user_inactive") {
        setError("Your account is deactivated. Contact support.");
      } else {
        setError("Could not log in. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Sign in to TaxMind Books</Text>
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
        accessibilityLabel="password"
        placeholder="Password"
        secureTextEntry
        autoComplete="password"
        value={password}
        onChangeText={setPassword}
        style={styles.input}
      />
      {error !== null && <Text style={styles.error}>{error}</Text>}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="sign-in"
        onPress={onSubmit}
        disabled={submitting || email.length === 0 || password.length === 0}
        style={({ pressed }) => [
          styles.button,
          (submitting || email.length === 0 || password.length === 0) &&
            styles.buttonDisabled,
          pressed && styles.buttonPressed,
        ]}
      >
        {submitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>Sign in</Text>
        )}
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="go-to-register"
        onPress={onNavigateToRegister}
      >
        <Text style={styles.link}>Create an account</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 24,
    justifyContent: "center",
    gap: 12,
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
