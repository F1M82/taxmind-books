import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import BalanceSheetScreen from "../../../src/screens/reports/BalanceSheetScreen";
import ProfitLossScreen from "../../../src/screens/reports/ProfitLossScreen";
import TrialBalanceScreen from "../../../src/screens/reports/TrialBalanceScreen";

jest.setTimeout(15000);

const mockGetPeriods = jest.fn();
const mockGetProfitLoss = jest.fn();
const mockGetBalanceSheet = jest.fn();
const mockGetTrialBalance = jest.fn();

jest.mock("../../../src/api/reports", () => ({
  getReportPeriods: (...a: unknown[]) => mockGetPeriods(...a),
  getProfitLoss: (...a: unknown[]) => mockGetProfitLoss(...a),
  getBalanceSheet: (...a: unknown[]) => mockGetBalanceSheet(...a),
  getTrialBalance: (...a: unknown[]) => mockGetTrialBalance(...a),
}));

const PERIODS = {
  items: [
    { label: "FY 2026-27", from_date: "2026-04-01", to_date: "2026-09-28", is_current: true },
    { label: "FY 2025-26", from_date: "2025-04-01", to_date: "2026-03-31", is_current: false },
    { label: "FY 2024-25", from_date: "2024-04-01", to_date: "2025-03-31", is_current: false },
  ],
};

const PNL = {
  from_date: "2025-04-01",
  to_date: "2026-03-31",
  income: { ledgers: [], total: "0.00" },
  expense: { ledgers: [], total: "0.00" },
  net: { value: "0.00", type: "profit" },
};

const TB = {
  as_of_date: "2026-03-31",
  company_id: "c",
  ledgers: [],
  totals: { total_dr: "0.00", total_cr: "0.00", in_balance: true },
  exclusions: {
    optional_vouchers_excluded_count: 0,
    cancelled_vouchers_excluded_count: 0,
  },
};

const BS = {
  as_of_date: "2026-03-31",
  assets: { groups: [], total: "0.00" },
  liabilities: { groups: [], total: "0.00" },
  current_period_profit_loss: { value: "0.00", type: "profit" },
  equation: { assets: "0.00", liabilities_plus_pnl: "0.00", in_balance: true },
};

beforeEach(() => {
  mockGetPeriods.mockReset().mockResolvedValue(PERIODS);
  mockGetProfitLoss.mockReset().mockResolvedValue(PNL);
  mockGetBalanceSheet.mockReset().mockResolvedValue(BS);
  mockGetTrialBalance.mockReset().mockResolvedValue(TB);
});

test("profit & loss: choosing a year loads that year's from/to dates", async () => {
  const { findByLabelText, getByLabelText } = render(<ProfitLossScreen />);
  fireEvent.press(await findByLabelText("fy-FY 2025-26"));
  await waitFor(() =>
    expect(mockGetProfitLoss).toHaveBeenLastCalledWith({
      from_date: "2025-04-01",
      to_date: "2026-03-31",
    }),
  );
  expect(getByLabelText("from-date").props.value).toBe("2025-04-01");
  expect(getByLabelText("to-date").props.value).toBe("2026-03-31");
  expect(
    getByLabelText("fy-FY 2025-26").props.accessibilityState.selected,
  ).toBe(true);
});

test("profit & loss: typing a custom date clears the year selection", async () => {
  const { findByLabelText, getByLabelText } = render(<ProfitLossScreen />);
  fireEvent.press(await findByLabelText("fy-FY 2025-26"));
  fireEvent.changeText(getByLabelText("from-date"), "2025-06-01");
  await waitFor(() =>
    expect(
      getByLabelText("fy-FY 2025-26").props.accessibilityState.selected,
    ).toBe(false),
  );
});

test("trial balance: choosing a year loads as-of = that year's end", async () => {
  const { findByLabelText } = render(<TrialBalanceScreen />);
  fireEvent.press(await findByLabelText("fy-FY 2024-25"));
  await waitFor(() =>
    expect(mockGetTrialBalance).toHaveBeenLastCalledWith({
      as_of_date: "2025-03-31",
    }),
  );
});

test("balance sheet: choosing a year loads as-of = that year's end", async () => {
  const { findByLabelText } = render(<BalanceSheetScreen />);
  fireEvent.press(await findByLabelText("fy-FY 2025-26"));
  await waitFor(() =>
    expect(mockGetBalanceSheet).toHaveBeenLastCalledWith({
      as_of_date: "2026-03-31",
    }),
  );
});

test("a failed periods fetch does not break the report screen", async () => {
  mockGetPeriods.mockReset().mockRejectedValue(new Error("boom"));
  const { queryByLabelText, getByLabelText } = render(<ProfitLossScreen />);
  await waitFor(() => expect(mockGetProfitLoss).toHaveBeenCalled());
  expect(queryByLabelText("fy-FY 2025-26")).toBeNull();
  // Manual date entry still works.
  expect(getByLabelText("from-date")).toBeTruthy();
});

test("a single-year company shows no picker", async () => {
  mockGetPeriods.mockReset().mockResolvedValue({ items: [PERIODS.items[0]] });
  const { queryByLabelText } = render(<TrialBalanceScreen />);
  await waitFor(() => expect(mockGetTrialBalance).toHaveBeenCalled());
  expect(queryByLabelText("fy-FY 2026-27")).toBeNull();
});
