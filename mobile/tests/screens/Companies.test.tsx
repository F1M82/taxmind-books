/**
 * Smoke tests for the Company list / create screens (P0.30).
 */
import {
  act,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react-native";
import React from "react";

import { ApiError } from "../../src/api/client";
import CompanyCreateScreen from "../../src/screens/companies/CompanyCreateScreen";
import CompanyListScreen from "../../src/screens/companies/CompanyListScreen";

const mockListCompanies = jest.fn();
const mockCreateCompany = jest.fn();
const mockSetActive = jest.fn();
const mockRefreshMe = jest.fn();
const mockMapTallyCompany = jest.fn();

jest.mock("../../src/api/companies", () => ({
  listCompanies: (...args: unknown[]) => mockListCompanies(...args),
  createCompany: (...args: unknown[]) => mockCreateCompany(...args),
}));

jest.mock("../../src/api/connector", () => ({
  mapTallyCompany: (...args: unknown[]) => mockMapTallyCompany(...args),
}));

jest.mock("../../src/context/CompanyContext", () => ({
  useActiveCompany: () => ({
    activeCompanyId: "c1",
    loading: false,
    setActive: mockSetActive,
    reconcile: jest.fn(),
  }),
}));

jest.mock("../../src/context/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: "u",
      email: "u@x.com",
      full_name: "U",
      is_ca: false,
      firm_name: null,
      is_active: true,
      companies: [],
    },
    loading: false,
    signIn: jest.fn(),
    signUp: jest.fn(),
    signOut: jest.fn(),
    refreshMe: mockRefreshMe,
  }),
}));

beforeEach(() => {
  mockListCompanies.mockReset();
  mockCreateCompany.mockReset();
  mockSetActive.mockReset();
  mockRefreshMe.mockReset();
  mockMapTallyCompany.mockReset();
  mockMapTallyCompany.mockResolvedValue({});
});

test("CompanyListScreen renders rows and marks the active one", async () => {
  mockListCompanies.mockResolvedValueOnce({
    items: [
      {
        id: "c1",
        name: "Acme",
        gstin: "27AAAAA1234A1Z5",
        status: "active",
        your_role: "owner",
      },
      {
        id: "c2",
        name: "Beta",
        gstin: null,
        status: "active",
        your_role: "viewer",
      },
    ],
    meta: { next_cursor: null, total: 2 },
  });
  const { findByText, getAllByText } = render(
    <CompanyListScreen onCreate={jest.fn()} onPick={jest.fn()} />,
  );
  await findByText("Acme");
  await findByText("Beta");
  // The active badge appears only on the active company.
  expect(getAllByText("active")).toHaveLength(1);
});

test("CompanyListScreen.onPick switches the active company", async () => {
  mockListCompanies.mockResolvedValueOnce({
    items: [
      {
        id: "c2",
        name: "Beta",
        gstin: null,
        status: "active",
        your_role: "viewer",
      },
    ],
    meta: { next_cursor: null, total: 1 },
  });
  const onPick = jest.fn();
  const { findByLabelText } = render(
    <CompanyListScreen onCreate={jest.fn()} onPick={onPick} />,
  );
  const card = await findByLabelText("select-c2");
  await act(async () => {
    fireEvent.press(card);
  });
  expect(mockSetActive).toHaveBeenCalledWith("c2");
  expect(onPick).toHaveBeenCalled();
});

test("CompanyCreateScreen submits and switches active company", async () => {
  mockCreateCompany.mockResolvedValueOnce({
    id: "c-new",
    name: "Created",
    gstin: null,
    pan: null,
    financial_year_start: "2026-04-01",
    status: "active",
    address: null,
    city: null,
    state_code: null,
    pincode: null,
    accounting_source: "standalone",
    created_at: "now",
    your_role: "owner",
  });
  const onCreated = jest.fn();
  const { getByLabelText } = render(
    <CompanyCreateScreen onCreated={onCreated} onCancel={jest.fn()} />,
  );
  fireEvent.changeText(getByLabelText("company-name"), "Created");
  await act(async () => {
    fireEvent.press(getByLabelText("create"));
  });
  await waitFor(() => {
    expect(mockCreateCompany).toHaveBeenCalled();
  });
  expect(mockRefreshMe).toHaveBeenCalled();
  expect(mockSetActive).toHaveBeenCalledWith("c-new");
  expect(onCreated).toHaveBeenCalled();
});

test("CompanyListScreen surfaces a persistent Tally-setup entry point", async () => {
  mockListCompanies.mockResolvedValueOnce({
    items: [
      { id: "c1", name: "Acme", gstin: null, status: "active", your_role: "owner" },
    ],
    meta: { next_cursor: null, total: 1 },
  });
  const onOpenTallySetup = jest.fn();
  const { findByLabelText } = render(
    <CompanyListScreen
      onCreate={jest.fn()}
      onPick={jest.fn()}
      onOpenTallySetup={onOpenTallySetup}
    />,
  );
  const button = await findByLabelText("open-tally-setup");
  fireEvent.press(button);
  expect(onOpenTallySetup).toHaveBeenCalled();
});

test("CompanyListScreen maps a reviewed discovery to the selected company", async () => {
  mockListCompanies.mockResolvedValueOnce({
    items: [{ id: "c2", name: "Beta", gstin: null, status: "active", your_role: "owner" }],
    meta: { next_cursor: null, total: 1 },
  });
  const { findByLabelText } = render(
    <CompanyListScreen onCreate={jest.fn()} onPick={jest.fn()} pendingDiscoveryId="discovery-1" />,
  );
  await act(async () => fireEvent.press(await findByLabelText("select-c2")));
  await waitFor(() => expect(mockMapTallyCompany).toHaveBeenCalledWith("discovery-1", "c2"));
  expect(mockSetActive).toHaveBeenCalledWith("c2");
});

test("CompanyListScreen attempts the mapping before switching active company, and shows the collision error if it fails", async () => {
  mockListCompanies.mockResolvedValueOnce({
    items: [{ id: "c2", name: "Beta", gstin: null, status: "active", your_role: "owner" }],
    meta: { next_cursor: null, total: 1 },
  });
  // Regression test for a real bug: switching active company BEFORE the
  // mapping call succeeded remounted the whole app stack mid-request
  // (CompanyContext bumps activeCompanyVersion, used as the stack
  // navigator's key), so a failure landed on a screen instance the user
  // could no longer see -- it silently looked like nothing happened.
  const order: string[] = [];
  mockSetActive.mockImplementation(async () => {
    order.push("setActive");
  });
  mockMapTallyCompany.mockImplementation(async () => {
    order.push("mapTallyCompany");
    const err = new ApiError(409, {
      error: { code: "tally_mapping_collision", message: "Company is already mapped to another Tally company." },
      request_id: "req-1",
    });
    throw err;
  });
  const { findByLabelText, findByText } = render(
    <CompanyListScreen onCreate={jest.fn()} onPick={jest.fn()} pendingDiscoveryId="discovery-1" />,
  );
  await act(async () => fireEvent.press(await findByLabelText("select-c2")));
  await findByText(/already linked/);
  expect(order).toEqual(["mapTallyCompany"]);
  expect(mockSetActive).not.toHaveBeenCalled();
});
