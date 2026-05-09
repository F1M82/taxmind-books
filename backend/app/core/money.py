"""Money primitives.

The single rule from `docs/MONEY.md`: every monetary value is a
`Decimal`. End to end — DB column, ORM, service layer, schemas, JSON.
No floats, no paise-as-int, no string-concat arithmetic.

This module provides:

- `configure_decimal_context()` — sets process-wide precision/rounding
- `MoneyColumn` — the canonical SQLAlchemy column type (NUMERIC(15, 2))
- `money_column()` — typed helper for `Mapped[Decimal]` columns
- `TWO_PLACES` and `quantize_money()` — quantization helpers
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import TYPE_CHECKING

from sqlalchemy import Numeric
from sqlalchemy.orm import mapped_column

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped


# Prec 28 is decimal's default — far more than any MSME balance needs.
# ROUND_HALF_EVEN ("banker's rounding") is the IFRS / Ind AS standard.
DECIMAL_PREC = 28


def configure_decimal_context() -> None:
    """Configure the process-wide Decimal context.

    Called once at FastAPI app startup (`app.main.create_app`) and once
    in the Celery worker bootstrap.
    """
    ctx = getcontext()
    ctx.prec = DECIMAL_PREC
    ctx.rounding = ROUND_HALF_EVEN


# ------------------------------------------------------------------
# Column type
# ------------------------------------------------------------------

# `MoneyColumn` is the only acceptable SQL column type for money.
# Models that use plain `Numeric(...)` are flagged by check_money_types.py.
MoneyColumn = Numeric(15, 2)


def money_column(
    *,
    nullable: bool = False,
    default: str | None = None,
) -> Mapped[Decimal]:
    """SQLAlchemy 2.0-style typed money column.

    Usage:
        class Voucher(Base):
            total_amount: Mapped[Decimal] = money_column()
            balance: Mapped[Decimal] = money_column(default="0.00")

    Defaults are passed as strings ("0.00") and converted via Decimal —
    never as floats. Per MONEY.md §"Migration of money data".
    """
    return mapped_column(  # type: ignore[return-value]
        MoneyColumn,
        nullable=nullable,
        default=Decimal(default) if default is not None else None,
    )


# ------------------------------------------------------------------
# Quantization
# ------------------------------------------------------------------

TWO_PLACES = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    """Quantize a Decimal to 2 places using banker's rounding.

    Use before persisting computed values and before equality checks.
    """
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)
