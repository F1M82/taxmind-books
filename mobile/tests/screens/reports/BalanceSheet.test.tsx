import { render, waitFor } from "@testing-library/react-native";
import React from "react";

import BalanceSheetScreen from "../../../src/screens/reports/BalanceSheetScreen";

jest.setTimeout(15000);

const mockGetBalanceSheet = jest.fn();

jest.mock("../../../src/api/reports", () => ({
  getBalanceSheet: (...args: unknown[]) => mockGetBalanceSheet(...args),
}));

beforeEach(() => {
  mockGetBalanceSheet.mockReset();
});


test("renders asset and liability groups with totals and the equation", async () => {
  mockGetBalanceSheet.mockResolvedValue({
    as_of_date: "2026-05-12",
    assets: {
      groups: [
        {
          group_name: "Bank Accounts",
          ledgers: [
            { ledger_id: "l-1", ledger_name: "HDFC", amount: "10000.00" },
          ],
          total: "10000.00",
        },
      ],
      total: "10000.00",
    },
    liabilities: {
      groups: [
        {
          group_name: "Sundry Creditors",
          ledgers: [
            {
              ledger_id: "l-2",
              ledger_name: "Vendor Co",
              amount: "3000.00",
            },
          ],
          total: "3000.00",
        },
      ],
      total: "3000.00",
    },
    current_period_profit_loss: { value: "7000.00", type: "profit" },
    equation: {
      assets: "10000.00",
      liabilities_plus_equity: "10000.00",
      in_balance: true,
    },
  });

  const { findByText, getByLabelText } = render(<BalanceSheetScreen />);

  await waitFor(() => expect(mockGetBalanceSheet).toHaveBeenCalled());

  await findByText("HDFC");
  await findByText("Vendor Co");
  await findByText("Bank Accounts");
  expect(getByLabelText("equation")).toBeTruthy();
});


test("surfaces a friendly error when the API returns 500 unbalanced", async () => {
  mockGetBalanceSheet.mockRejectedValue(new Error("unbalanced"));

  const { findByText } = render(<BalanceSheetScreen />);

  await findByText(/Could not load balance sheet/);
});


test("shows previous years' profit as its own row when non-zero", async () => {
  mockGetBalanceSheet.mockResolvedValue({
    as_of_date: "2027-04-30",
    assets: { groups: [], total: "13500.00" },
    liabilities: { groups: [], total: "10000.00" },
    current_period_profit_loss: { value: "500.00", type: "profit" },
    prior_periods_profit_loss: { value: "3000.00", type: "profit" },
    equation: {
      assets: "13500.00",
      liabilities_plus_equity: "13500.00",
      in_balance: true,
    },
  });
  const { findByText, getByLabelText } = render(<BalanceSheetScreen />);
  await findByText("Previous years profit");
  expect(getByLabelText("prior-pnl-row")).toBeTruthy();
});

test("hides the previous-years row when it is zero", async () => {
  mockGetBalanceSheet.mockResolvedValue({
    as_of_date: "2026-04-30",
    assets: { groups: [], total: "0.00" },
    liabilities: { groups: [], total: "0.00" },
    current_period_profit_loss: { value: "0.00", type: "profit" },
    prior_periods_profit_loss: { value: "0.00", type: "profit" },
    equation: { assets: "0.00", liabilities_plus_equity: "0.00", in_balance: true },
  });
  const { findByText, queryByLabelText } = render(<BalanceSheetScreen />);
  await findByText("Current period profit");
  expect(queryByLabelText("prior-pnl-row")).toBeNull();
});
