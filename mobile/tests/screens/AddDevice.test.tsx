import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import { ApiError } from "../../src/api/client";
import AddDeviceScreen from "../../src/screens/admin/AddDeviceScreen";

const mockIssueCode = jest.fn();

jest.mock("../../src/api/connector", () => ({
  issueEnrollmentCode: (...args: unknown[]) => mockIssueCode(...args),
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

const CODE_RESP = {
  code: "abc123XYZ",
  expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
  company_id: "backend-1",
};

beforeEach(() => {
  mockIssueCode.mockReset();
  mockIssueCode.mockResolvedValue(CODE_RESP);
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "owner" }];
});

test("owner can generate and see an enrollment code", async () => {
  const { findByLabelText, queryByLabelText } = render(<AddDeviceScreen />);

  expect(queryByLabelText("enrollment-code")).toBeNull();
  fireEvent.press(await findByLabelText("issue-enrollment-code"));

  const codeText = await findByLabelText("enrollment-code");
  expect(codeText.props.children).toBe("abc123XYZ");
  expect(mockIssueCode).toHaveBeenCalledTimes(1);
});

test("non-owner sees a note instead of the generate button", async () => {
  mockUserCompanies = [{ id: "backend-1", name: "Mine", role: "admin" }];
  const { findByText, queryByLabelText } = render(<AddDeviceScreen />);

  await findByText("Only an owner can add a new device.");
  expect(queryByLabelText("issue-enrollment-code")).toBeNull();
  expect(mockIssueCode).not.toHaveBeenCalled();
});

test("shows the backend error message when issuing fails", async () => {
  mockIssueCode.mockRejectedValue(
    new ApiError(403, {
      error: { code: "insufficient_role", message: "owner role required" },
      request_id: "r1",
    }),
  );
  const { findByLabelText, findByText } = render(<AddDeviceScreen />);

  fireEvent.press(await findByLabelText("issue-enrollment-code"));

  await findByText("owner role required");
});

test("expired code offers to generate another", async () => {
  mockIssueCode.mockResolvedValue({
    ...CODE_RESP,
    expires_at: new Date(Date.now() - 1000).toISOString(), // already expired
  });
  const { findByLabelText, queryByLabelText } = render(<AddDeviceScreen />);

  fireEvent.press(await findByLabelText("issue-enrollment-code"));

  await waitFor(() => {
    expect(queryByLabelText("issue-another-code")).not.toBeNull();
  });
  expect(queryByLabelText("enrollment-code")).toBeNull();
});
