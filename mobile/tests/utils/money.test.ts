/** Unit tests for utils/money.ts (no React deps). */
import {
  formatINR,
  moneyEquals,
  normalizeMoneyInput,
  formatDrCr,
  isNegativeAmount,
  negativeAmountStyle,
  NEGATIVE_AMOUNT_COLOR,
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


describe("formatDrCr (signed Dr-positive value -> Dr/Cr)", () => {
  test("positive is a debit", () => {
    expect(formatDrCr("733801.87")).toBe("₹7,33,801.87 Dr");
  });
  test("negative is a credit, shown without a minus sign", () => {
    expect(formatDrCr("-139818.21")).toBe("₹1,39,818.21 Cr");
  });
  test("zero has no side", () => {
    expect(formatDrCr("0.00")).toBe("₹0.00");
    expect(formatDrCr("-0.00")).toBe("₹0.00");
  });
  test("blank and invalid input", () => {
    expect(formatDrCr("")).toBe("—");
    expect(formatDrCr(null)).toBe("—");
    expect(formatDrCr("abc")).toBe("abc");
  });
});

describe("negative amounts are red", () => {
  test("isNegativeAmount", () => {
    expect(isNegativeAmount("-0.01")).toBe(true);
    expect(isNegativeAmount("0.00")).toBe(false);
    expect(isNegativeAmount("5.00")).toBe(false);
    expect(isNegativeAmount("")).toBe(false);
    expect(isNegativeAmount(undefined)).toBe(false);
    expect(isNegativeAmount("abc")).toBe(false);
  });
  test("negativeAmountStyle is red only for negatives", () => {
    expect(negativeAmountStyle("-50.00")).toEqual({
      color: NEGATIVE_AMOUNT_COLOR,
    });
    expect(negativeAmountStyle("50.00")).toBeUndefined();
    expect(negativeAmountStyle(null)).toBeUndefined();
  });
});
