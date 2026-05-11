/**
 * Smoke test for LoginScreen. The full e2e is in P0.32+ once Detox /
 * Maestro is in place; this is the unit-level harness that catches
 * "I broke the form" regressions.
 */
import {
  act,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react-native";
import React from "react";

import LoginScreen from "../../src/screens/auth/LoginScreen";

const mockSignIn = jest.fn();

jest.mock("../../src/context/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    signIn: mockSignIn,
    signUp: jest.fn(),
    signOut: jest.fn(),
    refreshMe: jest.fn(),
  }),
}));

beforeEach(() => {
  mockSignIn.mockReset();
});

test("submit button is disabled until both fields have values", () => {
  const onNavigateToRegister = jest.fn();
  const { getByLabelText } = render(
    <LoginScreen onNavigateToRegister={onNavigateToRegister} />,
  );
  const submit = getByLabelText("sign-in");
  // Disabled initially (Pressable's accessibilityState.disabled).
  expect(submit.props.accessibilityState?.disabled).toBeTruthy();

  fireEvent.changeText(getByLabelText("email"), "alice@example.com");
  fireEvent.changeText(getByLabelText("password"), "hunter2-pwd");
  expect(submit.props.accessibilityState?.disabled).toBeFalsy();
});

test("calls signIn with email+password on submit", async () => {
  mockSignIn.mockResolvedValueOnce(undefined);
  const onNavigateToRegister = jest.fn();
  const { getByLabelText } = render(
    <LoginScreen onNavigateToRegister={onNavigateToRegister} />,
  );
  fireEvent.changeText(getByLabelText("email"), "alice@example.com");
  fireEvent.changeText(getByLabelText("password"), "hunter2-pwd");
  await act(async () => {
    fireEvent.press(getByLabelText("sign-in"));
  });
  expect(mockSignIn).toHaveBeenCalledWith("alice@example.com", "hunter2-pwd");
});

test("shows a localized error on invalid_credentials", async () => {
  const { ApiError } = jest.requireActual("../../src/api/client");
  mockSignIn.mockRejectedValueOnce(
    new ApiError(401, {
      error: { code: "invalid_credentials", message: "wrong" },
      request_id: "req-1",
    }),
  );
  const { getByLabelText, findByText } = render(
    <LoginScreen onNavigateToRegister={jest.fn()} />,
  );
  fireEvent.changeText(getByLabelText("email"), "alice@example.com");
  fireEvent.changeText(getByLabelText("password"), "wrong-pwd-123");
  await act(async () => {
    fireEvent.press(getByLabelText("sign-in"));
  });
  await waitFor(() => findByText("Invalid email or password."));
});

test("navigates to register on link tap", () => {
  const onNavigateToRegister = jest.fn();
  const { getByLabelText } = render(
    <LoginScreen onNavigateToRegister={onNavigateToRegister} />,
  );
  fireEvent.press(getByLabelText("go-to-register"));
  expect(onNavigateToRegister).toHaveBeenCalled();
});
