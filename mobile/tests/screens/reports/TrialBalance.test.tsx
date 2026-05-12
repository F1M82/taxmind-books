import { render, waitFor } from "@testing-library/react-native";
import React from "react";

import TrialBalanceScreen from "../../../src/screens/reports/TrialBalanceScreen";

jest.setTimeout(15000);

const mockGetTrialBalance = jest.fn();

jest.mock("../../../src/api/reports", () => ({
  getTrialBalance: (...args: unknown[]) => mockGetTrialBalance(...args),
}));

beforeEach(() => {
  mockGetTrialBalance.mockReset();
});


test("renders ledgers, totals, and the in-balance flag", async () => {
  mockGetTrialBalance.mockResolvedValue({
    as_of_date: "2026-05-12",
    company_id: "c-1",
    ledgers: [
      {
        ledger_id: "l-1",
        ledger_name: "Bank",
        group_name: "Bank Accounts",
        opening_balance: "0.00",
        opening_balance_type: "Dr",
        period_dr: "1500.00",
        period_cr: "0.00",
        closing_balance: "1500.00",
        closing_balance_type: "Dr",
      },
      {
        ledger_id: "l-2",
        ledger_name: "Sharma Traders",
        group_name: "Sundry Debtors",
        opening_balance: "0.00",
        opening_balance_type: "Dr",
        period_dr: "0.00",
        period_cr: "1500.00",
        closing_balance: "1500.00",
        closing_balance_type: "Cr",
      },
    ],
    totals: { total_dr: "1500.00", total_cr: "1500.00", in_balance: true },
    exclusions: {
      optional_vouchers_excluded_count: 0,
      cancelled_vouchers_excluded_count: 0,
    },
  });

  const { findByText, getByLabelText } = render(<TrialBalanceScreen />);

  await waitFor(() => expect(mockGetTrialBalance).toHaveBeenCalled());

  await findByText("Bank");
  await findByText("Sharma Traders");
  const flag = getByLabelText("in-balance");
  expect(flag.props.children).toBe("In balance");
});


test("surfaces an error message when the API fails", async () => {
  mockGetTrialBalance.mockRejectedValue(new Error("nope"));

  const { findByText } = render(<TrialBalanceScreen />);

  await findByText("Could not load trial balance.");
});
