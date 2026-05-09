# Money Handling

**Status:** Frozen. Mandatory across all code paths that touch monetary values.

This document is non-negotiable. Money bugs in a financial product are not bugs — they are losses. The constitution Section 4 requires this; this document specifies *how*.

## The single rule

> Every value representing currency is a `Decimal`. End to end. From DB column, through ORM, through service layer, through Pydantic schema, through JSON response, into the client. No `float`, no `int` paise math, no string concatenation arithmetic.

## Why this is not negotiable

`float` cannot represent decimal fractions exactly. `0.1 + 0.2 != 0.3` is the canonical demonstration. In a system that posts vouchers to a CA's client's books, a single rounding error compounds across thousands of entries and produces inexplicable trial-balance differences. The CA loses confidence; the customer churns; the platform's reputation suffers.

Integer-paise arithmetic (multiplying by 100, working in paise as `int`) is *correct* but introduces conversion code at every boundary, which becomes the new bug surface. We use `Decimal` and avoid both classes of error.

## The canonical type

In Python: `decimal.Decimal` with global precision context set to 28 digits and `ROUND_HALF_EVEN` (banker's rounding). 28 digits is `decimal`'s default and exceeds anything we need; banker's rounding is the IFRS / Ind AS standard for accounting.

```python
# backend/app/core/money.py
from decimal import Decimal, getcontext, ROUND_HALF_EVEN

# Set once at process start. Imported by app.main.
def configure_decimal_context() -> None:
    ctx = getcontext()
    ctx.prec = 28
    ctx.rounding = ROUND_HALF_EVEN
```

`app.main.py` calls `configure_decimal_context()` before instantiating the FastAPI app. The Celery worker's bootstrap does the same.

## The canonical column type

In SQLAlchemy: `Numeric(15, 2)` — 15 total digits, 2 after the decimal point. Supports values up to ₹9,999,999,999,999.99. Sufficient for any realistic Indian MSME balance (largest practical use case is a few hundred crores, which is 12 digits).

```python
# backend/app/core/money.py
from sqlalchemy import Numeric
from sqlalchemy.orm import mapped_column, Mapped
from decimal import Decimal

MoneyColumn = Numeric(15, 2)

# Helper for type-annotated columns (SQLAlchemy 2.0 style):
def money_column(*, nullable: bool = False, default: str | None = None) -> Mapped[Decimal]:
    return mapped_column(
        MoneyColumn,
        nullable=nullable,
        default=Decimal(default) if default else None,
    )
```

All money columns in `models/` use `money_column()`. No exceptions.

## The canonical Pydantic field

For request/response schemas, money is `Decimal` with explicit constraints:

```python
# backend/app/schemas/common.py
from decimal import Decimal
from typing import Annotated
from pydantic import Field, BeforeValidator

def _validate_money(v):
    """Coerce string/int to Decimal; reject float."""
    if isinstance(v, float):
        raise ValueError("money values must not be float; pass string or Decimal")
    return Decimal(str(v))

Money = Annotated[
    Decimal,
    BeforeValidator(_validate_money),
    Field(
        ge=Decimal("0"),                       # money is non-negative; sign carried in entry_type
        max_digits=15,
        decimal_places=2,
        examples=["1500.00", "50000.50"],
    ),
]

# For values that may be negative (e.g., balance differences):
SignedMoney = Annotated[
    Decimal,
    BeforeValidator(_validate_money),
    Field(max_digits=15, decimal_places=2),
]
```

Usage in schemas:

```python
# backend/app/schemas/voucher.py
from app.schemas.common import Money

class VoucherCreate(BaseModel):
    voucher_type: VoucherType
    date: date
    total_amount: Money         # not Decimal, not float, not str — Money
    narration: str | None = None
```

The `_validate_money` validator rejects `float` at the API boundary. A client sending `{"total_amount": 1500.99}` (which JSON parses as `float`) gets a 422 with a clear error. A client sending `{"total_amount": "1500.99"}` (string, JSON-safe) succeeds.

## The canonical JSON serialization

Pydantic by default serializes `Decimal` to a JSON number, which the client then parses as `float`. This silently re-introduces the problem we just prevented.

Solution: configure Pydantic to serialize `Decimal` as a JSON string. The mobile/web clients also treat money as string and only convert when displaying.

```python
# backend/app/schemas/common.py
from pydantic import BaseModel, ConfigDict
from decimal import Decimal

class TaxMindBooksBase(BaseModel):
    """Base for all schemas. Configures Decimal serialization."""
    model_config = ConfigDict(
        json_encoders={Decimal: str},   # Decimal -> "1500.00" in JSON
        from_attributes=True,
    )
```

Every schema in `app/schemas/` inherits from `TaxMindBooksBase`, never directly from `BaseModel`. This is checked in CI (see Enforcement below).

## Money on the client (mobile/web)

In TypeScript, all money fields are `string`, not `number`. Conversion to displayable form happens at the render boundary using a small helper:

```typescript
// mobile/src/utils/money.ts
import Big from 'big.js';   // npm: big.js, deterministic decimal arithmetic

export type Money = string;   // canonical representation

export function moneyAdd(a: Money, b: Money): Money {
  return new Big(a).plus(b).toFixed(2);
}

export function moneySubtract(a: Money, b: Money): Money {
  return new Big(a).minus(b).toFixed(2);
}

export function formatINR(m: Money): string {
  // "1500.00" -> "₹ 1,500.00" using Indian numbering (1,00,000 not 100,000)
  const big = new Big(m);
  const [intPart, decPart] = big.toFixed(2).split('.');
  // Indian grouping: last 3 digits, then groups of 2
  const lastThree = intPart.slice(-3);
  const rest = intPart.slice(0, -3);
  const grouped = rest.length
    ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + lastThree
    : lastThree;
  return `₹ ${grouped}.${decPart}`;
}
```

`big.js` is the only client-side arithmetic library. Native `+` and `-` on money strings are forbidden. CI lints for this.

## Arithmetic rules

Inside the Python codebase, `Decimal` arithmetic is used directly. There are still rules:

1. **Never mix `Decimal` and `float`.** `Decimal("1.5") + 0.1` raises `TypeError`. Good. The ban exists to prevent code that "fixes" this with `float(d)` or `Decimal(f)` — both convert through float and lose precision.

2. **Never construct `Decimal` from `float`.** `Decimal(1.1)` is `Decimal('1.100000000000000088817841970012523233890533447265625')`. Always go through `str`: `Decimal(str(1.1))` is `Decimal('1.1')`. Better still, never have a float in the first place.

3. **Quantize before persistence and before comparison.** All money in the DB has 2 decimal places. After arithmetic, quantize to 2 places before passing to the ORM:
   ```python
   from decimal import Decimal
   TWO_PLACES = Decimal("0.01")
   total = (subtotal + tax).quantize(TWO_PLACES)
   ```

4. **Comparison uses `==` after quantization.** `Decimal("1.50") == Decimal("1.5")` is `True` mathematically but they have different `as_tuple()`. For business logic, quantize both sides, then compare.

5. **Tolerance comparison uses `abs(a - b) <= tolerance`** with tolerance as a `Decimal`. The reconciliation engine uses `Decimal("0.01")` as default tolerance for "amounts equal."

## Currency

**v1: INR only.** Multi-currency is an explicit non-feature per the architecture document Section 11.

Every money column is implicitly INR. There is no `currency` column. There is no `currencies` table. A future migration to multi-currency would add the column; v1 does not.

API responses include the currency code in metadata, not per field, to avoid surprising clients who later support multi-currency:

```json
{
  "data": { "total_amount": "1500.00" },
  "meta": { "currency": "INR" }
}
```

## Display rounding vs. storage rounding

- **Storage:** always 2 decimal places. Quantize at persistence.
- **Computation:** full Decimal precision until the final step. No intermediate rounding.
- **Display:** 2 decimal places, Indian grouping (₹ 1,00,000.00), via `formatINR()`.
- **GST computation:** computed at the line-item level, then summed, then quantized. CGST/SGST/IGST split is computed at the voucher level and stored quantized. The sum of the three must equal the voucher's gst_total within Decimal("0.01"); otherwise the voucher is flagged for review.

## Forbidden patterns

The following code patterns are **forbidden**. CI will reject pull requests containing them.

```python
# FORBIDDEN — float arithmetic on money
total = price * 1.18

# FORBIDDEN — float-to-Decimal conversion
amount = Decimal(price_as_float)

# FORBIDDEN — eval/string-concat arithmetic
total = str(eval(f"{a} + {b}"))

# FORBIDDEN — paise as int
total_paise = int(price * 100)

# FORBIDDEN — Pydantic Decimal field without going through Money type
class Schema(BaseModel):
    amount: Decimal           # use Money instead

# FORBIDDEN — JSON-encoded Decimal as number (default Pydantic behavior)
class Schema(BaseModel):     # without inheriting TaxMindBooksBase
    amount: Decimal
```

```typescript
// FORBIDDEN — number for money in TypeScript
interface Voucher {
  total_amount: number;     // must be string
}

// FORBIDDEN — native arithmetic on money strings
const total = parseFloat(a) + parseFloat(b);     // use moneyAdd

// FORBIDDEN — toLocaleString for INR (does not produce Indian grouping correctly across browsers)
total.toLocaleString('en-IN');     // use formatINR
```

## Enforcement

Three layers of enforcement. All run in CI; all block merge.

### Layer 1: type system

- `mypy --strict` on `backend/`. The `Money` type alias and `money_column()` helper give `Decimal` types that the type checker enforces.
- `tsc --strict` on `mobile/` and `web/`. Money fields typed as `Money` (alias for `string`) prevent accidental `number` use.

### Layer 2: custom lint check

`tools/lint/check_money_types.py` is a small AST-based checker. It scans all files under `backend/app/` and flags:

- Any `float` annotation on a name matching `*amount*`, `*balance*`, `*total*`, `*price*`, `*tax*`, `*gst*`, `*tds*`, `*cgst*`, `*sgst*`, `*igst*`
- Any `Numeric(...)` SQLAlchemy column where precision != 15 or scale != 2 (use `MoneyColumn` instead)
- Any Pydantic `BaseModel` subclass not transitively inheriting from `TaxMindBooksBase`
- Any `Decimal(<float-literal>)` construction
- Any `int * 100` or `int / 100` near identifiers matching the money pattern (proxy for paise math)

The checker runs in CI. Failures block merge.

### Layer 3: contract tests

A dedicated test module `tests/unit/core/test_money_contract.py` includes:

- A test that constructs every Pydantic schema with a `float` value and asserts a 422-equivalent error
- A test that round-trips a `Decimal("1500.99")` through the API and asserts the response body contains `"1500.99"` as a string
- A test that asserts `0.1 + 0.2 == Decimal("0.3")` after going through the canonical `money_add` helper
- A test that the configured `decimal` context has prec=28 and rounding=ROUND_HALF_EVEN at process start

These tests are P0. They run on every CI invocation. They are not skippable.

## Migration of money data

Future migrations that touch money columns must:

1. Use `Numeric(15, 2)`, never `FLOAT`, `REAL`, or `DOUBLE PRECISION`
2. Specify default values as strings (`'0.00'`), not floats
3. Be reviewed against this document before merge

The migration template in `alembic/script.py.mako` includes a comment reminding the author of these rules.

## Test cases the human runs during validation

The validation report (see VALIDATION_REPORT.md) includes a Money section with these checks:

1. POST a voucher with `{"total_amount": 1500.99}` (float). Expected: 422 with clear error.
2. POST a voucher with `{"total_amount": "1500.99"}` (string). Expected: 201, body has `total_amount: "1500.99"`.
3. POST a voucher with `{"total_amount": "1500.999"}` (3 decimal places). Expected: 422.
4. POST a voucher with `{"total_amount": "-100.00"}` (negative). Expected: 422 unless the endpoint explicitly accepts SignedMoney.
5. Inspect the database row for the posted voucher. Expected: column type `numeric(15,2)`, value exactly `1500.99`.
6. SELECT total_amount and verify it is not stored as `1500.989999...` or similar float artifact.

If any check fails, money handling is broken and the phase does not pass.

## Relationship to GST and TDS

GST (CGST + SGST + IGST) and TDS computations follow the same rules. The architecture allocates dedicated columns for each tax component on `vouchers` and `ledger_entries`; all are `MoneyColumn`. Tax rates (e.g., 18%) are stored as `Numeric(5, 2)` — the rate, not money. Multiplication of money by rate produces money:

```python
gst_amount = (taxable_value * gst_rate / Decimal("100")).quantize(TWO_PLACES)
```

Order of operations: divide last. `(taxable * rate) / 100` and `taxable * (rate / 100)` differ at the cent for some inputs because of intermediate quantization.

## Summary

- One type: `Decimal` in Python, `string` in TypeScript.
- One column: `Numeric(15, 2)`.
- One Pydantic alias: `Money`.
- One serializer: `Decimal -> str` in JSON.
- Three enforcement layers: types, lint, tests.
- Zero exceptions.
