"""Shared schema primitives.

`TaxMindBooksBase` is the base class for every Pydantic schema in
`app/schemas/`. Direct inheritance from `pydantic.BaseModel` for schemas
that touch money is a lint failure (`tools/lint/check_money_types.py`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
)


def _validate_money(value: Any) -> Decimal:
    """Coerce str/int/Decimal to Decimal; reject float.

    Floats are rejected outright — silently converting a JSON number to
    Decimal re-introduces the float-precision bug we're trying to
    prevent at the API boundary. Clients must send money as strings.
    """
    if isinstance(value, bool):
        # `bool` is a subclass of `int` in Python; reject explicitly.
        raise ValueError("money values must not be bool")
    if isinstance(value, float):
        raise ValueError(
            "money values must not be float; pass a string or Decimal "
            "(e.g. \"1500.99\", not 1500.99)"
        )
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _serialize_money(value: Decimal) -> str:
    """Serialize Decimal to JSON as a 2-decimal-place string."""
    return f"{value:.2f}"


# Money is non-negative — sign is carried in the entry_type column for
# vouchers; a negative "amount" is almost always a bug. Use SignedMoney
# for legitimate signed values (balance differences, deltas).
Money = Annotated[
    Decimal,
    BeforeValidator(_validate_money),
    PlainSerializer(_serialize_money, return_type=str, when_used="json"),
    Field(
        ge=Decimal("0"),
        max_digits=15,
        decimal_places=2,
        examples=["1500.00", "50000.50"],
    ),
]

SignedMoney = Annotated[
    Decimal,
    BeforeValidator(_validate_money),
    PlainSerializer(_serialize_money, return_type=str, when_used="json"),
    Field(
        max_digits=15,
        decimal_places=2,
        examples=["-1500.00", "0.00", "1500.50"],
    ),
]


class TaxMindBooksBase(BaseModel):
    """Base for all TaxMind Books request/response schemas.

    Money/SignedMoney carry their own JSON serializer (PlainSerializer
    above), so the base class only needs cross-cutting behaviors:

    1. `from_attributes=True` lets schemas hydrate from ORM models.
    2. `str_strip_whitespace=True` normalizes free-text fields.
    3. `validate_assignment=True` re-runs validators on attribute set.
    """

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )
