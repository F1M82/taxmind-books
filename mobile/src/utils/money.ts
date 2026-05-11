/**
 * Money helpers per MONEY.md.
 *
 * Money on the wire is a 2-decimal string. UI display uses the
 * Indian numbering system (`1,23,45,678.00`) and a leading ₹.
 * Inputs from text fields are normalized before submission.
 */

const INR_FORMAT = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Format a money string ("1500.00") for display ("₹1,500.00"). */
export function formatINR(amount: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") {
    return "—";
  }
  const n = Number(amount);
  if (!Number.isFinite(n)) {
    return amount;
  }
  return `₹${INR_FORMAT.format(n)}`;
}

/**
 * Normalize user-entered text to the canonical 2-decimal wire string.
 * Returns null when the input doesn't look like a number.
 *
 * Rules:
 *   - Strip whitespace, ₹, and grouping commas.
 *   - Reject negatives (Money is non-negative; sign is in entry_type).
 *   - Reject >2 decimals.
 *   - Empty → null (caller decides whether that's allowed).
 */
export function normalizeMoneyInput(raw: string): string | null {
  const cleaned = raw.replace(/[\s₹,]/g, "");
  if (cleaned === "") return null;
  if (!/^[0-9]+(\.[0-9]{1,2})?$/.test(cleaned)) return null;
  if (cleaned.includes(".")) {
    const [whole, frac] = cleaned.split(".");
    return `${whole}.${(frac ?? "").padEnd(2, "0")}`;
  }
  return `${cleaned}.00`;
}

/** Two money strings equal as Decimal (string compare after canonicalize). */
export function moneyEquals(a: string, b: string): boolean {
  const na = normalizeMoneyInput(a);
  const nb = normalizeMoneyInput(b);
  return na !== null && na === nb;
}
