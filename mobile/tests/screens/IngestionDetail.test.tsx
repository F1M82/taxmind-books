import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { Alert } from "react-native";

import IngestionDetailScreen from "../../src/screens/ingestions/IngestionDetailScreen";

const mockGetIngestion = jest.fn();
const mockDiscardIngestion = jest.fn();

jest.mock("../../src/api/ingestions", () => ({
  getIngestion: (...args: unknown[]) => mockGetIngestion(...args),
  discardIngestion: (...args: unknown[]) => mockDiscardIngestion(...args),
}));

beforeEach(() => {
  mockGetIngestion.mockReset();
  mockDiscardIngestion.mockReset();
  jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
});

const REVIEW_INGESTION = {
  id: "i-1",
  company_id: "c-1",
  source: "photo",
  status: "review",
  content_type: "image/jpeg",
  content_size: 100,
  received_at: "2026-10-19T10:00:00+05:30",
  extracted_at: "2026-10-19T10:00:05+05:30",
  failed_at: null,
  failure_reason: null,
  posted_voucher_id: null,
  parsed_data: {
    vendor_name: "Sharma Traders",
    total_amount: "11800.00",
  },
  extraction_confidence: "0.300",
  extraction_flags: ["no_gstin"],
  created_at: "2026-10-19T10:00:00+05:30",
};

test("shows extracted fields and action buttons for a review-status ingestion", async () => {
  mockGetIngestion.mockResolvedValue(REVIEW_INGESTION);

  const { findByText, findByLabelText } = render(
    <IngestionDetailScreen
      ingestionId="i-1"
      onCreateVoucher={jest.fn()}
      onResolved={jest.fn()}
      onBack={jest.fn()}
    />,
  );

  await findByText("Sharma Traders");
  await findByText("11800.00");
  await findByLabelText("create-voucher-from-ingestion");
  await findByLabelText("discard-ingestion");
});

test("tapping create voucher passes the ingestion id through", async () => {
  mockGetIngestion.mockResolvedValue(REVIEW_INGESTION);
  const onCreateVoucher = jest.fn();

  const { findByLabelText } = render(
    <IngestionDetailScreen
      ingestionId="i-1"
      onCreateVoucher={onCreateVoucher}
      onResolved={jest.fn()}
      onBack={jest.fn()}
    />,
  );

  fireEvent.press(await findByLabelText("create-voucher-from-ingestion"));
  expect(onCreateVoucher).toHaveBeenCalledWith("i-1");
});

test("discard calls the API and resolves after confirming", async () => {
  mockGetIngestion.mockResolvedValue(REVIEW_INGESTION);
  mockDiscardIngestion.mockResolvedValue({
    ...REVIEW_INGESTION,
    status: "discarded",
  });
  const onResolved = jest.fn();

  (Alert.alert as jest.Mock).mockImplementation((_title, _msg, buttons) => {
    const discardButton = buttons?.find(
      (b: { text: string }) => b.text === "Discard",
    );
    discardButton?.onPress?.();
  });

  const { findByLabelText } = render(
    <IngestionDetailScreen
      ingestionId="i-1"
      onCreateVoucher={jest.fn()}
      onResolved={onResolved}
      onBack={jest.fn()}
    />,
  );

  await act(async () => {
    fireEvent.press(await findByLabelText("discard-ingestion"));
  });

  await waitFor(() => {
    expect(mockDiscardIngestion).toHaveBeenCalledWith("i-1");
    expect(onResolved).toHaveBeenCalled();
  });
});

test("a posted ingestion shows no action buttons", async () => {
  mockGetIngestion.mockResolvedValue({
    ...REVIEW_INGESTION,
    status: "posted",
    posted_voucher_id: "v-1",
  });

  const { queryByLabelText, findByText } = render(
    <IngestionDetailScreen
      ingestionId="i-1"
      onCreateVoucher={jest.fn()}
      onResolved={jest.fn()}
      onBack={jest.fn()}
    />,
  );

  await findByText("Linked voucher: v-1");
  expect(queryByLabelText("create-voucher-from-ingestion")).toBeNull();
  expect(queryByLabelText("discard-ingestion")).toBeNull();
});
