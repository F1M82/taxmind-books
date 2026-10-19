import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import CompanySettingsScreen from "../../src/screens/admin/CompanySettingsScreen";

const mockGetCompany = jest.fn();
const mockUpdateCompany = jest.fn();
const mockListLedgers = jest.fn();

jest.mock("../../src/api/companies", () => ({
  getCompany: (...a: unknown[]) => mockGetCompany(...a),
  updateCompany: (...a: unknown[]) => mockUpdateCompany(...a),
}));

jest.mock("../../src/api/ledgers", () => ({
  listLedgers: (...a: unknown[]) => mockListLedgers(...a),
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

const COMPANY_NO_DEFAULT = {
  id: "backend-1",
  name: "Mine",
  default_purchase_ledger_id: null,
};

beforeEach(() => {
  mockGetCompany.mockReset().mockResolvedValue(COMPANY_NO_DEFAULT);
  mockUpdateCompany.mockReset();
  mockListLedgers.mockReset().mockResolvedValue({
    items: [
      { id: "led-purch", name: "Purchase A/c", group_name: "Purchase" },
    ],
    meta: { next_cursor: null, total: 1 },
  });
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "owner" }];
});

test("non-admin sees a note instead of the picker", async () => {
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "accountant" }];
  const { findByText, queryByLabelText } = render(<CompanySettingsScreen />);

  await findByText("Only an owner or admin can change company settings.");
  expect(queryByLabelText("pick-default-purchase-ledger")).toBeNull();
});

test("shows 'Not set' when no default ledger is configured", async () => {
  const { findByText } = render(<CompanySettingsScreen />);
  await findByText("Not set — tap to choose");
});

test("picking a ledger saves it and updates the displayed name", async () => {
  mockUpdateCompany.mockResolvedValue({
    ...COMPANY_NO_DEFAULT,
    default_purchase_ledger_id: "led-purch",
  });

  const { findByLabelText, findByText } = render(<CompanySettingsScreen />);

  await act(async () => {
    fireEvent.press(await findByLabelText("pick-default-purchase-ledger"));
  });
  await act(async () => {
    fireEvent.press(await findByLabelText("pick-led-purch"));
  });

  await waitFor(() => {
    expect(mockUpdateCompany).toHaveBeenCalledWith("backend-1", {
      default_purchase_ledger_id: "led-purch",
    });
  });
  await findByText("Purchase A/c");
});
