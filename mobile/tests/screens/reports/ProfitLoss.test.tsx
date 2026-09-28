import { render, waitFor } from "@testing-library/react-native";
import React from "react";
import { StyleSheet } from "react-native";

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


test("shows opening and closing stock when a valuation applies", async () => {
  mockGetProfitLoss.mockResolvedValue({
    from_date: "2025-04-01",
    to_date: "2026-03-31",
    income: { ledgers: [], total: "500.00" },
    expense: { ledgers: [], total: "200.00" },
    stock: {
      opening_value: "700.00",
      closing_value: "400.00",
      source: "tally",
      captured_at: "2026-09-28T10:00:00Z",
    },
    net: { value: "0.00", type: "profit" },
  });
  const { findByText, getByLabelText } = render(<ProfitLossScreen />);
  await findByText("Opening stock");
  await findByText("Closing stock");
  expect(getByLabelText("stock-block")).toBeTruthy();
  await findByText("From Tally, included in the net result.");
});

test("shows no stock block when no valuation applies", async () => {
  mockGetProfitLoss.mockResolvedValue({
    from_date: "2025-04-01",
    to_date: "2025-12-31",
    income: { ledgers: [], total: "0.00" },
    expense: { ledgers: [], total: "0.00" },
    stock: null,
    net: { value: "0.00", type: "profit" },
  });
  const { findByLabelText, queryByLabelText } = render(<ProfitLossScreen />);
  await findByLabelText("net-row");
  expect(queryByLabelText("stock-block")).toBeNull();
});


test("stock rows show Dr/Cr and a credit closing value is red", async () => {
  mockGetProfitLoss.mockResolvedValue({
    from_date: "2025-04-01",
    to_date: "2026-03-31",
    income: { ledgers: [], total: "0.00" },
    expense: { ledgers: [], total: "0.00" },
    stock: {
      opening_value: "733801.87",
      closing_value: "-139818.21",
      source: "tally",
      captured_at: "2026-09-28T10:00:00Z",
    },
    net: { value: "873620.08", type: "loss" },
  });
  const { findByText } = render(<ProfitLossScreen />);
  const opening = await findByText("₹7,33,801.87 Dr");
  const closing = await findByText("₹1,39,818.21 Cr");
  expect(StyleSheet.flatten(closing.props.style).color).toBe("#c0392b");
  expect(StyleSheet.flatten(opening.props.style).color).not.toBe("#c0392b");
});
