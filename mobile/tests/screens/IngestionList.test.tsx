import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";

import IngestionListScreen from "../../src/screens/ingestions/IngestionListScreen";

const mockListIngestions = jest.fn();

jest.mock("../../src/api/ingestions", () => ({
  listIngestions: (...args: unknown[]) => mockListIngestions(...args),
}));

beforeEach(() => {
  mockListIngestions.mockReset();
});

test("renders a status badge per ingestion", async () => {
  mockListIngestions.mockResolvedValue({
    items: [
      {
        id: "i-1",
        source: "photo",
        status: "review",
        content_type: "image/jpeg",
        extraction_confidence: "0.300",
        extraction_flags: ["no_gstin"],
        received_at: "2026-10-19T10:00:00+05:30",
      },
      {
        id: "i-2",
        source: "photo",
        status: "posted",
        content_type: "image/jpeg",
        extraction_confidence: "0.950",
        extraction_flags: [],
        received_at: "2026-10-18T10:00:00+05:30",
      },
    ],
    meta: { next_cursor: null, total: 2 },
  });

  const { findByText } = render(
    <IngestionListScreen onOpenDetail={jest.fn()} />,
  );

  await findByText("Needs review");
  await findByText("Posted");
});

test("shows an empty state when there are no ingestions", async () => {
  mockListIngestions.mockResolvedValue({
    items: [],
    meta: { next_cursor: null, total: 0 },
  });

  const { findByText } = render(
    <IngestionListScreen onOpenDetail={jest.fn()} />,
  );

  await findByText("No scanned invoices yet.");
});

test("tapping a row navigates to its detail screen", async () => {
  mockListIngestions.mockResolvedValue({
    items: [
      {
        id: "i-1",
        source: "photo",
        status: "review",
        content_type: "image/jpeg",
        extraction_confidence: null,
        extraction_flags: [],
        received_at: "2026-10-19T10:00:00+05:30",
      },
    ],
    meta: { next_cursor: null, total: 1 },
  });

  const onOpenDetail = jest.fn();
  const { findByLabelText } = render(
    <IngestionListScreen onOpenDetail={onOpenDetail} />,
  );

  const row = await findByLabelText("open-ingestion-i-1");
  fireEvent.press(row);
  await waitFor(() => {
    expect(onOpenDetail).toHaveBeenCalledWith("i-1");
  });
});
