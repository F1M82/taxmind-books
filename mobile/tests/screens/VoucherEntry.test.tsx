/**
 * Smoke tests for VoucherEntryScreen (P0.31).
 *
 * Covers the Phase-0 happy path: pick two ledgers, enter equal
 * amounts on Dr/Cr lines, hit Post → createVoucher is called with
 * the right shape and an Idempotency-Key header.
 */
import {
  act,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react-native";
import React from "react";

import VoucherEntryScreen from "../../src/screens/vouchers/VoucherEntryScreen";

const mockCreateVoucher = jest.fn();
const mockListLedgers = jest.fn();

jest.mock("../../src/api/vouchers", () => ({
  createVoucher: (...args: unknown[]) => mockCreateVoucher(...args),
}));

jest.mock("../../src/api/ledgers", () => ({
  listLedgers: (...args: unknown[]) => mockListLedgers(...args),
}));

// crypto.getRandomValues isn't there by default in Jest's jsdom env;
// stub it.
beforeAll(() => {
  if ((globalThis as { crypto?: unknown }).crypto === undefined) {
    (globalThis as { crypto?: unknown }).crypto = {};
  }
  (globalThis as unknown as { crypto: { getRandomValues: (b: Uint8Array) => Uint8Array } }).crypto.getRandomValues =
    (buf: Uint8Array) => {
      for (let i = 0; i < buf.length; i += 1) {
        buf[i] = i;
      }
      return buf;
    };
});

beforeEach(() => {
  mockCreateVoucher.mockReset();
  mockListLedgers.mockReset();
});


test("submit button is disabled until Dr/Cr match and ledgers are picked", () => {
  const { getByLabelText } = render(
    <VoucherEntryScreen onCreated={jest.fn()} onCancel={jest.fn()} />,
  );
  const post = getByLabelText("post");
  expect(post.props.accessibilityState?.disabled).toBeTruthy();
});


test("creates a balanced Receipt with idempotency key", async () => {
  mockListLedgers.mockResolvedValue({
    items: [
      {
        id: "led-bank",
        name: "Bank",
        group_name: "Bank Accounts",
        opening_balance: "0.00",
        balance_type: "Dr",
        gstin: null,
        is_active: true,
      },
      {
        id: "led-party",
        name: "Sharma Traders",
        group_name: "Sundry Debtors",
        opening_balance: "0.00",
        balance_type: "Dr",
        gstin: null,
        is_active: true,
      },
    ],
    meta: { next_cursor: null, total: 2 },
  });
  mockCreateVoucher.mockResolvedValue({
    id: "v-1",
    voucher_type: "Receipt",
    voucher_number: null,
    date: "2026-05-11",
    narration: null,
    reference: null,
    total_amount: "1500.00",
    status: "posted",
    source: "manual",
    gst_applicable: false,
    tally_posted_at: null,
    created_at: "now",
  });

  const onCreated = jest.fn();
  const { getByLabelText, findByLabelText } = render(
    <VoucherEntryScreen onCreated={onCreated} onCancel={jest.fn()} />,
  );

  // Pick a ledger for entry 0 (Dr).
  await act(async () => {
    fireEvent.press(getByLabelText("pick-ledger-0"));
  });
  const pick0 = await findByLabelText("pick-led-bank");
  await act(async () => {
    fireEvent.press(pick0);
  });

  // Pick a ledger for entry 1 (Cr).
  await act(async () => {
    fireEvent.press(getByLabelText("pick-ledger-1"));
  });
  const pick1 = await findByLabelText("pick-led-party");
  await act(async () => {
    fireEvent.press(pick1);
  });

  // Enter equal amounts.
  fireEvent.changeText(getByLabelText("entry-amount-0"), "1500");
  fireEvent.changeText(getByLabelText("entry-amount-1"), "1500");

  // Post.
  await act(async () => {
    fireEvent.press(getByLabelText("post"));
  });

  await waitFor(() => {
    expect(mockCreateVoucher).toHaveBeenCalled();
  });

  const [body, idemKey] = mockCreateVoucher.mock.calls[0];
  expect(idemKey).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
  );
  expect(body.voucher_type).toBe("Receipt");
  expect(body.total_amount).toBe("1500.00");
  expect(body.entries).toEqual([
    { ledger_id: "led-bank", amount: "1500.00", entry_type: "Dr" },
    { ledger_id: "led-party", amount: "1500.00", entry_type: "Cr" },
  ]);
  expect(onCreated).toHaveBeenCalled();
});
