/** Unit tests for utils/money.ts (no React deps). */
import {
  formatINR,
  moneyEquals,
  normalizeMoneyInput,
} from "../../src/utils/money";


test("formatINR renders Indian grouping with rupee", () => {
  expect(formatINR("1500.00")).toContain("1,500.00");
  // Indian grouping: last 3 digits, then groups of 2 → 1,23,45,678.
  expect(formatINR("12345678.00")).toContain("1,23,45,678");
});

test("formatINR returns em-dash for missing", () => {
  expect(formatINR(null)).toBe("—");
  expect(formatINR(undefined)).toBe("—");
  expect(formatINR("")).toBe("—");
});

test("normalizeMoneyInput canonicalizes whole / 1-dec / 2-dec strings", () => {
  expect(normalizeMoneyInput("100")).toBe("100.00");
  expect(normalizeMoneyInput("100.5")).toBe("100.50");
  expect(normalizeMoneyInput("100.55")).toBe("100.55");
});

test("normalizeMoneyInput strips currency + commas + whitespace", () => {
  expect(normalizeMoneyInput(" ₹1,500.50 ")).toBe("1500.50");
});

test("normalizeMoneyInput rejects 3+ decimals and negatives", () => {
  expect(normalizeMoneyInput("100.555")).toBeNull();
  expect(normalizeMoneyInput("-100")).toBeNull();
});

test("normalizeMoneyInput rejects garbage", () => {
  expect(normalizeMoneyInput("abc")).toBeNull();
  expect(normalizeMoneyInput("")).toBeNull();
});

test("moneyEquals compares normalized values", () => {
  expect(moneyEquals("100", "100.00")).toBe(true);
  expect(moneyEquals("100.5", "100.50")).toBe(true);
  expect(moneyEquals("100", "101")).toBe(false);
});
