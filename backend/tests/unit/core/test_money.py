"""Money contract tests per docs/MONEY.md."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal, getcontext

import pytest
from app.core.money import (
    DECIMAL_PREC,
    TWO_PLACES,
    configure_decimal_context,
    quantize_money,
)
from app.schemas.common import Money, SignedMoney, TaxMindBooksBase
from pydantic import ValidationError

# ----------------------------------------------------------------------
# configure_decimal_context()
# ----------------------------------------------------------------------


def test_configure_decimal_context_sets_prec_and_rounding() -> None:
    configure_decimal_context()
    ctx = getcontext()
    assert ctx.prec == DECIMAL_PREC
    assert ctx.prec == 28
    assert ctx.rounding == ROUND_HALF_EVEN


# ----------------------------------------------------------------------
# Money type — validation
# ----------------------------------------------------------------------


class _MoneyHolder(TaxMindBooksBase):
    amount: Money


class _SignedMoneyHolder(TaxMindBooksBase):
    delta: SignedMoney


def test_money_accepts_string() -> None:
    h = _MoneyHolder.model_validate({"amount": "1500.99"})
    assert h.amount == Decimal("1500.99")


def test_money_accepts_int() -> None:
    h = _MoneyHolder.model_validate({"amount": 1500})
    assert h.amount == Decimal("1500")


def test_money_accepts_decimal_instance() -> None:
    h = _MoneyHolder.model_validate({"amount": Decimal("99.50")})
    assert h.amount == Decimal("99.50")


def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _MoneyHolder.model_validate({"amount": 1500.99})
    assert "float" in str(excinfo.value).lower()


def test_money_rejects_bool() -> None:
    with pytest.raises(ValidationError):
        _MoneyHolder.model_validate({"amount": True})


def test_money_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        _MoneyHolder.model_validate({"amount": "-1.00"})


def test_money_rejects_three_decimal_places() -> None:
    with pytest.raises(ValidationError):
        _MoneyHolder.model_validate({"amount": "1500.999"})


def test_money_rejects_overlong() -> None:
    # 16 total digits = 14 before decimal + 2 after exceeds max_digits=15
    with pytest.raises(ValidationError):
        _MoneyHolder.model_validate({"amount": "12345678901234.99"})


def test_signed_money_accepts_negative() -> None:
    h = _SignedMoneyHolder.model_validate({"delta": "-100.00"})
    assert h.delta == Decimal("-100.00")


def test_signed_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        _SignedMoneyHolder.model_validate({"delta": -100.0})


# ----------------------------------------------------------------------
# Money type — JSON serialization
# ----------------------------------------------------------------------


def test_money_serializes_as_string_with_two_decimals() -> None:
    h = _MoneyHolder.model_validate({"amount": "1500"})
    payload = json.loads(h.model_dump_json())
    assert payload == {"amount": "1500.00"}
    assert isinstance(payload["amount"], str)


def test_money_serializes_preserves_two_decimals() -> None:
    h = _MoneyHolder.model_validate({"amount": "1500.50"})
    payload = json.loads(h.model_dump_json())
    assert payload == {"amount": "1500.50"}


def test_signed_money_serializes_negative() -> None:
    h = _SignedMoneyHolder.model_validate({"delta": "-100.50"})
    payload = json.loads(h.model_dump_json())
    assert payload == {"delta": "-100.50"}


# ----------------------------------------------------------------------
# Arithmetic + quantization
# ----------------------------------------------------------------------


def test_decimal_add_does_not_lose_precision_like_float() -> None:
    # The canonical demonstration: 0.1 + 0.2 != 0.3 in float.
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


def test_quantize_money_rounds_half_to_even() -> None:
    # 2.5 → 2 (banker's), 3.5 → 4 (banker's). At cent precision:
    # 1.005 → 1.00 (next-even), 1.015 → 1.02 (next-even)
    assert quantize_money(Decimal("1.005")) == Decimal("1.00")
    assert quantize_money(Decimal("1.015")) == Decimal("1.02")


def test_quantize_money_two_places_constant_is_one_cent() -> None:
    assert Decimal("0.01") == TWO_PLACES


def test_quantize_money_idempotent_on_already_quantized() -> None:
    assert quantize_money(Decimal("1500.99")) == Decimal("1500.99")
