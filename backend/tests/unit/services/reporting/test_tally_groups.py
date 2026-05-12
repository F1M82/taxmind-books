"""Unit tests for the Tally-group classifier (P0.38)."""

from __future__ import annotations

from app.services.reporting.tally_groups import (
    GroupClass,
    classify,
    is_asset,
    is_expense,
    is_income,
    is_liability,
    is_sundry_creditors,
    is_sundry_debtors,
)


def test_classify_income_groups() -> None:
    for name in ("Direct Incomes", "Indirect Incomes", "Sales Accounts"):
        assert classify(name) is GroupClass.income
        assert is_income(name) is True


def test_classify_expense_groups() -> None:
    for name in (
        "Direct Expenses",
        "Indirect Expenses",
        "Purchase Accounts",
    ):
        assert classify(name) is GroupClass.expense
        assert is_expense(name) is True


def test_classify_asset_groups() -> None:
    for name in (
        "Bank Accounts",
        "Cash-in-hand",
        "Sundry Debtors",
        "Fixed Assets",
        "Stock-in-hand",
    ):
        assert classify(name) is GroupClass.asset
        assert is_asset(name) is True


def test_classify_liability_groups() -> None:
    for name in (
        "Sundry Creditors",
        "Capital Account",
        "Reserves & Surplus",
        "Duties & Taxes",
        "Secured Loans",
    ):
        assert classify(name) is GroupClass.liability
        assert is_liability(name) is True


def test_classify_unknown_returns_unclassified() -> None:
    assert classify("Made-Up Group") is GroupClass.unclassified
    assert classify(None) is GroupClass.unclassified
    assert classify("") is GroupClass.unclassified


def test_classify_is_case_insensitive_and_strips() -> None:
    assert classify("  bank accounts  ") is GroupClass.asset
    assert classify("SUNDRY DEBTORS") is GroupClass.asset
    assert is_sundry_debtors("  Sundry Debtors  ") is True
    assert is_sundry_creditors("sundry creditors") is True


def test_a_group_is_in_one_class_only() -> None:
    """Sanity check: no name appears in two of the four primary buckets."""
    from app.services.reporting.tally_groups import (
        ASSET_GROUPS,
        EXPENSE_GROUPS,
        INCOME_GROUPS,
        LIABILITY_GROUPS,
    )

    overlaps = (
        (INCOME_GROUPS & EXPENSE_GROUPS)
        | (INCOME_GROUPS & ASSET_GROUPS)
        | (INCOME_GROUPS & LIABILITY_GROUPS)
        | (EXPENSE_GROUPS & ASSET_GROUPS)
        | (EXPENSE_GROUPS & LIABILITY_GROUPS)
        | (ASSET_GROUPS & LIABILITY_GROUPS)
    )
    assert overlaps == frozenset()
