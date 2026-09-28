import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import { StyleSheet } from "react-native";

import { ApiError } from "../../src/api/client";
import StockValuationScreen from "../../src/screens/admin/StockValuationScreen";

const mockList = jest.fn();
const mockPull = jest.fn();

jest.mock("../../src/api/stockValuation", () => ({
  listStockValuations: (...a: unknown[]) => mockList(...a),
  pullStockValuation: (...a: unknown[]) => mockPull(...a),
}));

jest.mock("../../src/context/CompanyContext", () => ({
  useActiveCompany: () => ({ activeCompanyId: "backend-1" }),
}));

let mockUserCompanies: { id: string; name: string; role: string }[] = [
  { id: "backend-1", name: "Mine", role: "owner" },
];
jest.mock("../../src/context/AuthContext", () => ({
  useAuth: () => ({ user: { companies: mockUserCompanies } }),
}));

const FY25 = {
  id: "v1",
  label: "FY 2025-26",
  period_from: "2025-04-01",
  period_to: "2026-03-31",
  opening_value: "733801.87",
  closing_value: "-139818.21",
  source: "tally",
  item_count: 210,
  negative_stock_items: 18,
  captured_at: "2026-09-28T10:00:00Z",
};

beforeEach(() => {
  mockList.mockReset().mockResolvedValue({ items: [FY25] });
  mockPull.mockReset();
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "owner" }];
});

test("lists recorded years with opening and closing stock", async () => {
  const { findByText } = render(<StockValuationScreen />);
  await findByText("FY 2025-26");
  await findByText("₹7,33,801.87 Dr");
  // Net credit (negative stock in Tally) is shown as Cr.
  await findByText("₹1,39,818.21 Cr");
});

test("a credit (negative) value is red and a debit is not", async () => {
  const { findByText } = render(<StockValuationScreen />);
  const cr = await findByText("₹1,39,818.21 Cr");
  const dr = await findByText("₹7,33,801.87 Dr");
  expect(StyleSheet.flatten(cr.props.style).color).toBe("#c0392b");
  expect(StyleSheet.flatten(dr.props.style).color).not.toBe("#c0392b");
});

test("warns about items with negative stock in Tally", async () => {
  const { findByLabelText } = render(<StockValuationScreen />);
  const warn = await findByLabelText("negative-FY 2025-26");
  expect(JSON.stringify(warn.props.children)).toContain("18");
});

test("no warning when there are no negative-stock items", async () => {
  mockList.mockResolvedValue({ items: [{ ...FY25, negative_stock_items: 0 }] });
  const { findByText, queryByLabelText } = render(<StockValuationScreen />);
  await findByText("FY 2025-26");
  expect(queryByLabelText("negative-FY 2025-26")).toBeNull();
});

test("shows an empty state when nothing is recorded", async () => {
  mockList.mockResolvedValue({ items: [] });
  const { findByText } = render(<StockValuationScreen />);
  await findByText("No stock recorded yet.");
});

test("reading from Tally records the year and refreshes the list", async () => {
  mockPull.mockResolvedValue(FY25);
  mockList.mockResolvedValueOnce({ items: [] }).mockResolvedValue({ items: [FY25] });
  const { findByLabelText, findByText } = render(<StockValuationScreen />);
  fireEvent.press(await findByLabelText("pull-stock"));
  await findByText("Recorded FY 2025-26.");
  expect(mockPull).toHaveBeenCalledWith("backend-1");
  await findByText("₹7,33,801.87 Dr");
});

test("shows the backend's instruction when Tally's period is not one year", async () => {
  mockPull.mockRejectedValue(
    new ApiError(422, {
      error: {
        code: "stock_period_not_single_fy",
        message: "Set the period to a single financial year at the Gateway of Tally.",
      },
      request_id: "r1",
    }),
  );
  const { findByLabelText, findByText } = render(<StockValuationScreen />);
  fireEvent.press(await findByLabelText("pull-stock"));
  await findByText(
    "Set the period to a single financial year at the Gateway of Tally.",
  );
});

test("an accountant sees the list but no read button", async () => {
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "accountant" }];
  const { findByText, queryByLabelText } = render(<StockValuationScreen />);
  await findByText("FY 2025-26");
  expect(queryByLabelText("pull-stock")).toBeNull();
  await findByText("Only an owner or admin can read stock from Tally.");
  expect(mockPull).not.toHaveBeenCalled();
});

test("a failed list load shows an error instead of hanging", async () => {
  mockList.mockRejectedValue(new Error("network"));
  const { findByLabelText } = render(<StockValuationScreen />);
  await waitFor(async () => {
    expect((await findByLabelText("pull-error")).props.children).toBe(
      "Could not load stock valuations.",
    );
  });
});
