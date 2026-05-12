import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import OnboardingScreen from "../../src/screens/onboarding/OnboardingScreen";

const mockGetOnboardingChecklist = jest.fn();

jest.mock("../../src/api/onboarding", () => ({
  getOnboardingChecklist: (...args: unknown[]) =>
    mockGetOnboardingChecklist(...args),
}));

const HANDLERS = {
  onOpenLedgers: jest.fn(),
  onOpenNewVoucher: jest.fn(),
};

beforeEach(() => {
  mockGetOnboardingChecklist.mockReset();
  Object.values(HANDLERS).forEach((h) => h.mockReset());
});

const baseChecklist = {
  company_id: "c-1",
  items: [
    {
      key: "company_created",
      label: "Create your company",
      completed: true,
      completed_at: "2026-05-01T10:00:00Z",
    },
    {
      key: "connector_installed",
      label: "Install Tally Connector",
      completed: false,
    },
    {
      key: "ledgers_synced",
      label: "Sync ledgers from Tally",
      completed: false,
    },
    {
      key: "first_voucher_posted",
      label: "Post your first voucher",
      completed: false,
    },
    {
      key: "first_invoice_extracted",
      label: "Try invoice scan (Phase 1+)",
      completed: false,
    },
  ],
  completed_count: 1,
  total_count: 5,
};


test("renders all five items with the right completion state", async () => {
  mockGetOnboardingChecklist.mockResolvedValue(baseChecklist);

  const { getByLabelText, findByText } = render(
    <OnboardingScreen {...HANDLERS} />,
  );

  await waitFor(() =>
    expect(mockGetOnboardingChecklist).toHaveBeenCalled(),
  );
  await findByText("1 of 5 complete");

  expect(getByLabelText("item-company_created")).toBeTruthy();
  expect(getByLabelText("item-connector_installed")).toBeTruthy();
  expect(getByLabelText("item-ledgers_synced")).toBeTruthy();
  expect(getByLabelText("item-first_voucher_posted")).toBeTruthy();
  expect(getByLabelText("item-first_invoice_extracted")).toBeTruthy();
});


test("tapping an incomplete ledgers_synced item navigates to Ledgers", async () => {
  mockGetOnboardingChecklist.mockResolvedValue(baseChecklist);

  const { findByLabelText } = render(<OnboardingScreen {...HANDLERS} />);

  const button = await findByLabelText("open-ledgers_synced");
  await act(async () => {
    fireEvent.press(button);
  });

  expect(HANDLERS.onOpenLedgers).toHaveBeenCalled();
  expect(HANDLERS.onOpenNewVoucher).not.toHaveBeenCalled();
});


test("tapping first_voucher_posted navigates to NewVoucher", async () => {
  mockGetOnboardingChecklist.mockResolvedValue(baseChecklist);

  const { findByLabelText } = render(<OnboardingScreen {...HANDLERS} />);

  const button = await findByLabelText("open-first_voucher_posted");
  await act(async () => {
    fireEvent.press(button);
  });

  expect(HANDLERS.onOpenNewVoucher).toHaveBeenCalled();
});


test("completed items are not pressable", async () => {
  mockGetOnboardingChecklist.mockResolvedValue(baseChecklist);

  const { findByLabelText, queryByLabelText } = render(
    <OnboardingScreen {...HANDLERS} />,
  );

  // company_created is completed in baseChecklist; no Pressable
  // wrapper should be emitted for it (open-company_created absent).
  await findByLabelText("item-company_created");
  expect(queryByLabelText("open-company_created")).toBeNull();
});


test("first_invoice_extracted has no tap target even when not completed", async () => {
  mockGetOnboardingChecklist.mockResolvedValue(baseChecklist);

  const { findByLabelText, queryByLabelText } = render(
    <OnboardingScreen {...HANDLERS} />,
  );
  await findByLabelText("item-first_invoice_extracted");
  expect(queryByLabelText("open-first_invoice_extracted")).toBeNull();
});


test("surfaces an error when the API call fails", async () => {
  mockGetOnboardingChecklist.mockRejectedValue(new Error("nope"));

  const { findByText } = render(<OnboardingScreen {...HANDLERS} />);

  await findByText("Could not load onboarding checklist.");
});
