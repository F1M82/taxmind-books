import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import TallySetupScreen from "../../src/screens/onboarding/TallySetupScreen";

const mockGetStatus = jest.fn();
const mockGetCompanies = jest.fn();
const mockMapCompany = jest.fn();
const mockSetActive = jest.fn();

jest.mock("../../src/api/connector", () => ({
  getConnectorStatus: (...args: unknown[]) => mockGetStatus(...args),
  getTallyCompanies: (...args: unknown[]) => mockGetCompanies(...args),
  mapTallyCompany: (...args: unknown[]) => mockMapCompany(...args),
}));

jest.mock("../../src/context/CompanyContext", () => ({
  useActiveCompany: () => ({ activeCompanyId: "backend-1", activeCompanyVersion: 0, setActive: mockSetActive }),
}));

const status = { company_id: "backend-1", connector_id: "connector-1", connected: true };
const discovery = {
  connector_id: "connector-1",
  tally_data_folder_path: "C:/Tally/Data",
  scanned_at: null,
  companies: [
   { discovery_id: "discovery-1", tally_company_identifier: "tally-1", tally_company_name: "Acme Tally", tally_master_id: "hidden-guid", gstin: null, financial_year_start: null, mapped_to_backend_company_id: null },
   { discovery_id: "discovery-2", tally_company_identifier: "tally-2", tally_company_name: "Other Company", tally_master_id: "hidden-guid-2", gstin: null, financial_year_start: null, mapped_to_backend_company_id: "backend-2" },
  ],
};

beforeEach(() => {
  mockGetStatus.mockReset();
  mockGetCompanies.mockReset();
  mockMapCompany.mockReset();
  mockSetActive.mockReset();
  mockGetStatus.mockResolvedValue(status);
  mockGetCompanies.mockResolvedValue(discovery);
  mockMapCompany.mockResolvedValue({});
});

test("renders discovered mapped and unmapped states without credential inputs", async () => {
  const { findByText, queryByLabelText } = render(<TallySetupScreen />);

  await findByText("Acme Tally");
  expect(await findByText("Unmapped")).toBeTruthy();
  expect(await findByText("Mapped to another company")).toBeTruthy();
  expect(queryByLabelText("tally-guid")).toBeNull();
  expect(queryByLabelText("tally-name")).toBeNull();
  expect(queryByLabelText("tally-path")).toBeNull();
  expect(queryByLabelText("tally-token")).toBeNull();
  expect(mockGetCompanies).toHaveBeenCalledWith("connector-1", false);
});

test("unmapped selection requires explicit review and never maps the active company", async () => {
  const { findByLabelText } = render(<TallySetupScreen onReviewExistingCompany={jest.fn()} />);
  fireEvent.press(await findByLabelText("tally-company-discovery-1"));
  await findByLabelText("review-existing-company");
  expect(mockMapCompany).not.toHaveBeenCalled();
  expect(mockSetActive).not.toHaveBeenCalled();
});

test("mapped selection switches the active backend company", async () => {
  const { findByLabelText } = render(<TallySetupScreen />);
  await act(async () => fireEvent.press(await findByLabelText("tally-company-discovery-2")));
  expect(mockSetActive).toHaveBeenCalledWith("backend-2");
});

test("offers company creation while preserving the company switcher path", async () => {
  const onCreateCompany = jest.fn();
  const { findByLabelText } = render(<TallySetupScreen onCreateCompany={onCreateCompany} />);
   fireEvent.press(await findByLabelText("tally-company-discovery-1"));
   fireEvent.press(await findByLabelText("create-company-for-tally"));
   expect(onCreateCompany).toHaveBeenCalledWith("discovery-1");
});
