import { render, waitFor } from "@testing-library/react-native";
import React from "react";

import ProfitLossScreen from "../../../src/screens/reports/ProfitLossScreen";

jest.setTimeout(15000);

const mockGetProfitLoss = jest.fn();

jest.mock("../../../src/api/reports", () => ({
  getProfitLoss: (...args: unknown[]) => mockGetProfitLoss(...args),
}));

beforeEach(() => {
  mockGetProfitLoss.mockReset();
});


test("renders income, expense, and net = profit when positive", async () => {
  mockGetProfitLoss.mockResolvedValue({
    from_date: "2026-04-01",
    to_date: "2026-05-12",
    income: {
      ledgers: [{ ledger_id: "l-1", ledger_name: "Sales", amount: "5000.00" }],
      total: "5000.00",
    },
    expense: {
      ledgers: [
        { ledger_id: "l-2", ledger_name: "Rent", amount: "1200.00" },
      ],
      total: "1200.00",
    },
    net: { value: "3800.00", type: "profit" },
  });

  const { findByText, getByLabelText } = render(<ProfitLossScreen />);

  await waitFor(() => expect(mockGetProfitLoss).toHaveBeenCalled());
  await findByText("Sales");
  await findByText("Rent");
  expect(getByLabelText("net-row")).toBeTruthy();
  await findByText("Net Profit");
});


test("renders net = loss when net.type is loss", async () => {
  mockGetProfitLoss.mockResolvedValue({
    from_date: "2026-04-01",
    to_date: "2026-05-12",
    income: { ledgers: [], total: "0.00" },
    expense: {
      ledgers: [{ ledger_id: "l-2", ledger_name: "Rent", amount: "1200.00" }],
      total: "1200.00",
    },
    net: { value: "1200.00", type: "loss" },
  });

  const { findByText } = render(<ProfitLossScreen />);

  await findByText("Net Loss");
});
