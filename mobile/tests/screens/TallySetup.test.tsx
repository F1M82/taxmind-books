/**
 * P3.7 Phase 7C mobile — Tally Setup screen tests.
 *
 * Covers: connector state display, Tally company discovery display,
 * GUID display, explicit mapping confirmation, mapped-state update
 * (backend-driven only), conflict surfacing, network/backend error
 * handling, and the no-ledger-sync / no-local-fake-mapping invariants.
 */
import {
  act,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react-native";
import React from "react";

import { ApiError } from "../../src/api/client";
import TallySetupScreen from "../../src/screens/onboarding/TallySetupScreen";

const mockGetConnectorStatus = jest.fn();
const mockGetCompanyMapping = jest.fn();
const mockConfirmCompanyMapping = jest.fn();

jest.mock("../../src/api/connector", () => ({
  getConnectorStatus: (...args: unknown[]) =>
    mockGetConnectorStatus(...args),
  getCompanyMapping: (...args: unknown[]) => mockGetCompanyMapping(...args),
  confirmCompanyMapping: (...args: unknown[]) =>
    mockConfirmCompanyMapping(...args),
}));

const API_MODULES = jest.requireMock("../../src/api/connector") as Record<
  string,
  unknown
>;

beforeEach(() => {
  mockGetConnectorStatus.mockReset();
  mockGetCompanyMapping.mockReset();
  mockConfirmCompanyMapping.mockReset();
});

const connectedStatus = {
  company_id: "c-1",
  connected: true,
  last_seen_at: null,
  tally_running: true,
  tally_version: "5.6",
  connector_version: "0.1.0",
  queued_outbound_count: null,
};

const unmapped = {
  company_id: "c-1",
  tally_master_id: null,
  mapped: false,
};

const mapped = {
  company_id: "c-1",
  tally_master_id: "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9",
  mapped: true,
};

const VIGHNA = "Vighnaharta Agro Chemicals - FROM 1-APR-2025";
const GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9";


test("shows connector setup state when connected and Tally running", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);

  const { findByLabelText, findByText } = render(<TallySetupScreen />);

  const card = await findByLabelText("connector-status");
  expect(card).toBeTruthy();
  await findByText("Connected — Tally is running");
});


test("shows connector offline state", async () => {
  mockGetConnectorStatus.mockResolvedValue({
    company_id: "c-1",
    connected: false,
    last_seen_at: null,
    tally_running: null,
    tally_version: null,
    connector_version: null,
    queued_outbound_count: null,
  });
  mockGetCompanyMapping.mockResolvedValue(unmapped);

  const { findByText } = render(<TallySetupScreen />);

  await findByText("Not connected");
  await findByText(
    "Start the Tally Connector on your PC and pair it before proceeding.",
  );
});


test("displays the discovered Tally company name and GUID before confirm", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);

  const { getByLabelText, findByText } = render(<TallySetupScreen />);
  await findByText("Not mapped to a Tally company yet");

  fireEvent.changeText(getByLabelText("tally-name"), VIGHNA);
  fireEvent.changeText(getByLabelText("tally-guid"), GUID);

  expect(getByLabelText("tally-setup")).toBeTruthy();
  expect(getByLabelText("confirm-mapping")).toBeTruthy();
  // Review card carries exactly what will be bound.
  await findByText(VIGHNA);
  await findByText(GUID);
});


test("explicit confirm calls the backend with the GUID", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  mockConfirmCompanyMapping.mockResolvedValue({
    company_id: "c-1",
    tally_master_id: GUID,
    tally_company_name: VIGHNA,
  });

  const { getByLabelText, findByText } = render(<TallySetupScreen />);
  await findByText("Not mapped to a Tally company yet");

  fireEvent.changeText(getByLabelText("tally-name"), VIGHNA);
  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  await act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  await waitFor(() =>
    expect(mockConfirmCompanyMapping).toHaveBeenCalledWith({
      tally_company_guid: GUID,
      tally_company_name: VIGHNA,
    }),
  );
});


test("successful mapping updates setup state from the backend", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  mockConfirmCompanyMapping.mockResolvedValue({
    company_id: "c-1",
    tally_master_id: GUID,
    tally_company_name: VIGHNA,
  });

  const { getByLabelText, findByText, queryByLabelText } = render(
    <TallySetupScreen />,
  );
  await findByText("Not mapped to a Tally company yet");

  fireEvent.changeText(getByLabelText("tally-name"), VIGHNA);
  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  await act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  // Status reflects the backend response — not a local pre-flip.
  await findByText(`✓ Mapped to ${VIGHNA}`);
  expect(await findByText(GUID)).toBeTruthy();
  expect(queryByLabelText("confirm-mapping")).toBeNull();
});


test("mapping conflict is surfaced to the user", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  mockConfirmCompanyMapping.mockRejectedValue(
    new ApiError(409, {
      error: {
        code: "company_mapping_conflict",
        message: "GUID already mapped elsewhere",
      },
      request_id: "r-1",
    }),
  );

  const { getByLabelText, findByLabelText } = render(<TallySetupScreen />);
  await findByLabelText("tally-guid");

  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  await act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  const msg = await findByLabelText("mapping-error");
  expect(msg.props.children).toMatch(/Mapping conflict/);
});


test("authorization failure is surfaced", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  mockConfirmCompanyMapping.mockRejectedValue(
    new ApiError(403, {
      error: { code: "insufficient_permission", message: "owner only" },
      request_id: "r-2",
    }),
  );

  const { getByLabelText, findByLabelText } = render(<TallySetupScreen />);
  await findByLabelText("tally-guid");

  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  await act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  const msg = await findByLabelText("mapping-error");
  expect(String(msg.props.children)).toMatch(/owner access/i);
});


test("network/backend errors are handled without a permanent spinner", async () => {
  mockGetConnectorStatus.mockRejectedValue(new Error("offline"));
  mockGetCompanyMapping.mockRejectedValue(new Error("offline"));

  const { findByText, queryByTestId } = render(<TallySetupScreen />);

  await findByText(
    "Could not load Tally Setup. Check the backend and try again.",
  );
  expect(queryByTestId("activity-indicator")).toBeNull();
});


test("confirm failure during a live request surfaces an error, not a stuck state", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  mockConfirmCompanyMapping.mockRejectedValue(new Error("network down"));

  const { getByLabelText, findByLabelText } = render(<TallySetupScreen />);
  await findByLabelText("tally-guid");

  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  await act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  const msg = await findByLabelText("mapping-error");
  expect(String(msg.props.children)).toMatch(/Could not confirm mapping/i);
  // The form stays usable for a retry.
  expect(getByLabelText("confirm-mapping")).toBeTruthy();
});


test("no ledger sync is triggered by mapping", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(mapped);
  mockConfirmCompanyMapping.mockReset();

  const { findByText } = render(<TallySetupScreen />);
  await findByText(`✓ Mapped to a Tally company`);

  // The connector surface backs status, mapping read, and mapping
  // confirm only — nothing that pulls/syncs ledgers.
  expect(Object.keys(API_MODULES).sort()).toEqual([
    "confirmCompanyMapping",
    "getCompanyMapping",
    "getConnectorStatus",
  ]);
  expect(mockConfirmCompanyMapping).not.toHaveBeenCalled();
  expect(mockGetCompanyMapping).toHaveBeenCalled();
});


test("no local-only fake mapping: state flips only after backend resolves", async () => {
  mockGetConnectorStatus.mockResolvedValue(connectedStatus);
  mockGetCompanyMapping.mockResolvedValue(unmapped);
  let resolveConfirm!: (v: unknown) => void;
  mockConfirmCompanyMapping.mockReturnValue(
    new Promise((resolve) => {
      resolveConfirm = resolve;
    }),
  );

  const { getByLabelText, getByText, findByText } = render(
    <TallySetupScreen />,
  );
  await findByText("Not mapped to a Tally company yet");

  fireEvent.changeText(getByLabelText("tally-guid"), GUID);
  const pressPromise = act(async () => {
    fireEvent.press(getByLabelText("confirm-mapping"));
  });

  // While the request is in flight the mapping card must still report
  // unmapped — the UI cannot fabricate a mapping locally.
  expect(getByText("Not mapped to a Tally company yet")).toBeTruthy();

  resolveConfirm({
    company_id: "c-1",
    tally_master_id: GUID,
    tally_company_name: null,
  });
  await pressPromise;
  await findByText("✓ Mapped to a Tally company");
});