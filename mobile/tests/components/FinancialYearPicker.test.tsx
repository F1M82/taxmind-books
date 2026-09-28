import { fireEvent, render } from "@testing-library/react-native";
import React from "react";

import FinancialYearPicker from "../../src/components/reports/FinancialYearPicker";

const FY_26 = {
  label: "FY 2026-27",
  from_date: "2026-04-01",
  to_date: "2026-09-28",
  is_current: true,
};
const FY_25 = {
  label: "FY 2025-26",
  from_date: "2025-04-01",
  to_date: "2026-03-31",
  is_current: false,
};

test("renders one chip per financial year", () => {
  const { getByText } = render(
    <FinancialYearPicker
      periods={[FY_26, FY_25]}
      selectedLabel={null}
      onSelect={jest.fn()}
    />,
  );
  getByText("FY 2026-27");
  getByText("FY 2025-26");
});

test("pressing a chip reports the whole period", () => {
  const onSelect = jest.fn();
  const { getByLabelText } = render(
    <FinancialYearPicker
      periods={[FY_26, FY_25]}
      selectedLabel={null}
      onSelect={onSelect}
    />,
  );
  fireEvent.press(getByLabelText("fy-FY 2025-26"));
  expect(onSelect).toHaveBeenCalledWith(FY_25);
});

test("marks only the selected chip as selected", () => {
  const { getByLabelText } = render(
    <FinancialYearPicker
      periods={[FY_26, FY_25]}
      selectedLabel="FY 2025-26"
      onSelect={jest.fn()}
    />,
  );
  expect(
    getByLabelText("fy-FY 2025-26").props.accessibilityState.selected,
  ).toBe(true);
  expect(
    getByLabelText("fy-FY 2026-27").props.accessibilityState.selected,
  ).toBe(false);
});

test("renders nothing when there is only one year to choose", () => {
  const { toJSON } = render(
    <FinancialYearPicker
      periods={[FY_26]}
      selectedLabel={null}
      onSelect={jest.fn()}
    />,
  );
  expect(toJSON()).toBeNull();
});

test("renders nothing for an empty list", () => {
  const { toJSON } = render(
    <FinancialYearPicker periods={[]} selectedLabel={null} onSelect={jest.fn()} />,
  );
  expect(toJSON()).toBeNull();
});
