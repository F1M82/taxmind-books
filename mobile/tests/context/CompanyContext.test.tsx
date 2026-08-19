import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { Pressable, Text } from "react-native";

import { CompanyProvider, useActiveCompany } from "../../src/context/CompanyContext";

const mockGetActiveCompanyId = jest.fn();
const mockSetActiveCompanyId = jest.fn();
const user = {
  companies: [
    { id: "c1", role: "owner" },
    { id: "c2", role: "viewer" },
  ],
};

jest.mock("../../src/api/client", () => ({
  getActiveCompanyId: (...args: unknown[]) => mockGetActiveCompanyId(...args),
  setActiveCompanyId: (...args: unknown[]) => mockSetActiveCompanyId(...args),
}));

jest.mock("../../src/context/AuthContext", () => ({
  useAuth: () => ({
    authLoading: false,
    loading: false,
    user,
  }),
}));

function Probe(): React.ReactElement {
  const { activeCompanyVersion, setActive } = useActiveCompany();
  return (
    <>
      <Text accessibilityLabel="company-version">{activeCompanyVersion}</Text>
      <Pressable accessibilityLabel="switch-company" onPress={() => void setActive("c2")} />
    </>
  );
}

test("changing active company increments the cache version", async () => {
  mockGetActiveCompanyId.mockResolvedValue(null);
  mockSetActiveCompanyId.mockResolvedValue(undefined);
  const { findByLabelText } = render(
    <CompanyProvider>
      <Probe />
    </CompanyProvider>,
  );

  await waitFor(async () => expect((await findByLabelText("company-version")).props.children).toBe(1));
  await act(async () => fireEvent.press(await findByLabelText("switch-company")));
  expect((await findByLabelText("company-version")).props.children).toBe(2);
});
