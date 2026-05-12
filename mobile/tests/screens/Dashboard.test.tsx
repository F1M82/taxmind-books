/**
 * Smoke tests for the v1.2 DashboardScreen (P0.41).
 *
 * Mocks both the dashboard API and the auth/company contexts so
 * the screen can render without AsyncStorage or a real navigator.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import DashboardScreen from "../../src/screens/dashboard/DashboardScreen";

jest.setTimeout(15000);

const mockGetDashboardHome = jest.fn();
const mockGetOnboardingChecklist = jest.fn();

jest.mock("../../src/api/dashboard", () => ({
  getDashboardHome: (...args: unknown[]) => mockGetDashboardHome(...args),
}));

jest.mock("../../src/api/onboarding", () => ({
  getOnboardingChecklist: (...args: unknown[]) =>
    mockGetOnboardingChecklist(...args),
}));

jest.mock("../../src/context/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: "u-1",
      full_name: "Test User",
      email: "u@example.com",
      companies: [{ id: "c-1", name: "Acme Pvt Ltd", role: "owner" }],
    },
    signOut: jest.fn(),
  }),
}));

jest.mock("../../src/context/CompanyContext", () => ({
  useActiveCompany: () => ({ activeCompanyId: "c-1", loading: false }),
}));

const HANDLERS = {
  onOpenCompanies: jest.fn(),
  onOpenLedgers: jest.fn(),
  onOpenVouchers: jest.fn(),
  onOpenTrialBalance: jest.fn(),
  onOpenProfitLoss: jest.fn(),
  onOpenBalanceSheet: jest.fn(),
  onOpenOutstanding: jest.fn(),
  onOpenOnboarding: jest.fn(),
};

beforeEach(() => {
  mockGetDashboardHome.mockReset();
  mockGetOnboardingChecklist.mockReset();
  // Default: onboarding past the hide threshold (4 of 5), so the
  // tile stays out of the way and existing tile-presence assertions
  // continue to pass. Tests that care about the tile override.
  mockGetOnboardingChecklist.mockResolvedValue({
    company_id: "c-1",
    items: [],
    completed_count: 4,
    total_count: 5,
  });
  Object.values(HANDLERS).forEach((h) => h.mockReset());
});

const sample = {
  as_of: "2026-05-12T10:00:00Z",
  company_name: "Acme Pvt Ltd",
  connector: {
    connected: true,
    tally_running: true,
    last_seen_seconds_ago: 5,
  },
  today: {
    vouchers_created: 3,
    vouchers_pending_approval: 1,
    cash_in: "1500.00",
    cash_out: "200.00",
  },
  this_month: {
    cash_in: "55000.00",
    cash_out: "12000.00",
    vouchers_created: 42,
    vouchers_pending_approval: 0,
  },
  outstanding: {
    receivables_total: "10000.00",
    payables_total: "3000.00",
  },
  gst_liability_indicative: { month_to_date: "1800.00" },
  alerts: [],
};


test("renders all tiles from the dashboard payload", async () => {
  mockGetDashboardHome.mockResolvedValue(sample);

  const { getByLabelText } = render(<DashboardScreen {...HANDLERS} />);

  await waitFor(() => expect(mockGetDashboardHome).toHaveBeenCalled());

  await waitFor(() => {
    expect(getByLabelText("tile-connector")).toBeTruthy();
  });
  expect(getByLabelText("tile-today")).toBeTruthy();
  expect(getByLabelText("tile-this-month")).toBeTruthy();
  expect(getByLabelText("tile-outstanding")).toBeTruthy();
  expect(getByLabelText("tile-gst")).toBeTruthy();
});


test("tapping the outstanding tile navigates to the outstanding screen", async () => {
  mockGetDashboardHome.mockResolvedValue(sample);

  const { getByLabelText } = render(<DashboardScreen {...HANDLERS} />);

  await waitFor(() => expect(mockGetDashboardHome).toHaveBeenCalled());
  const tile = await waitFor(() => getByLabelText("tile-outstanding"));

  await act(async () => {
    fireEvent.press(tile);
  });

  expect(HANDLERS.onOpenOutstanding).toHaveBeenCalled();
});


test("pending-approvals alert routes to the vouchers list when tapped", async () => {
  mockGetDashboardHome.mockResolvedValue({
    ...sample,
    alerts: [
      {
        kind: "pending_approvals",
        severity: "warning",
        message: "3 Optional vouchers awaiting approval",
        since: null,
      },
    ],
  });

  const { getByLabelText } = render(<DashboardScreen {...HANDLERS} />);

  await waitFor(() => expect(mockGetDashboardHome).toHaveBeenCalled());
  const alert = await waitFor(() =>
    getByLabelText("alert-pending_approvals"),
  );

  await act(async () => {
    fireEvent.press(alert);
  });

  expect(HANDLERS.onOpenVouchers).toHaveBeenCalled();
});


test("surfaces an error message when the API call fails", async () => {
  mockGetDashboardHome.mockRejectedValue(new Error("nope"));

  const { findByText } = render(<DashboardScreen {...HANDLERS} />);

  await findByText("Could not load dashboard.");
});
