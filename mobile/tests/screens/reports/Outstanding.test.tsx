import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import OutstandingScreen from "../../../src/screens/reports/OutstandingScreen";

jest.setTimeout(15000);

const mockGetOutstanding = jest.fn();

jest.mock("../../../src/api/reports", () => ({
  getOutstanding: (...args: unknown[]) => mockGetOutstanding(...args),
}));

beforeEach(() => {
  mockGetOutstanding.mockReset();
});


test("loads receivables by default and renders the party list with total", async () => {
  mockGetOutstanding.mockResolvedValue({
    type: "receivables",
    as_of_date: "2026-05-12",
    items: [
      {
        ledger_id: "l-1",
        ledger_name: "Sharma Traders",
        ledger_gstin: "27AABCS1234A1Z5",
        balance: "1500.00",
        balance_type: "Dr",
      },
    ],
    total: "1500.00",
    total_type: "Dr",
  });

  const { findByText, getByLabelText } = render(<OutstandingScreen />);

  await waitFor(() => expect(mockGetOutstanding).toHaveBeenCalled());

  const [firstCall] = mockGetOutstanding.mock.calls[0];
  expect(firstCall.type).toBe("receivables");

  await findByText("Sharma Traders");
  expect(getByLabelText("pick-receivables")).toBeTruthy();
});


test("switching to payables refetches with the new type", async () => {
  mockGetOutstanding.mockResolvedValue({
    type: "receivables",
    as_of_date: "2026-05-12",
    items: [],
    total: "0.00",
    total_type: "Dr",
  });

  const { getByLabelText } = render(<OutstandingScreen />);
  await waitFor(() => expect(mockGetOutstanding).toHaveBeenCalledTimes(1));

  mockGetOutstanding.mockResolvedValue({
    type: "payables",
    as_of_date: "2026-05-12",
    items: [
      {
        ledger_id: "l-9",
        ledger_name: "Vendor Co",
        ledger_gstin: null,
        balance: "800.00",
        balance_type: "Cr",
      },
    ],
    total: "800.00",
    total_type: "Cr",
  });

  await act(async () => {
    fireEvent.press(getByLabelText("pick-payables"));
  });

  await waitFor(() => expect(mockGetOutstanding).toHaveBeenCalledTimes(2));
  const [secondCall] = mockGetOutstanding.mock.calls[1];
  expect(secondCall.type).toBe("payables");
});
