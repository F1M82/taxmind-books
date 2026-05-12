"""Tally group classification — Income / Expense / Asset / Liability.

The sets below mirror Tally's standard chart of accounts groups per
docs/REPORTS.md §"Group classification". A `group_name` is the
human-readable label stored on `ledgers.group_name` (synced from
Tally's PARENT element).

Customer-defined groups inherit their parent's classification. For
Phase 0 we apply the lookup directly to the ledger's `group_name`;
group hierarchy walking is a Phase 1 concern.

Comparison is case-insensitive and trims whitespace — Tally is loose
about capitalization in user-created groups, and our connector copies
the raw string.
"""

from __future__ import annotations

from enum import Enum


class GroupClass(str, Enum):
    """Where a ledger's group rolls up in financial reports."""

    income = "income"
    expense = "expense"
    asset = "asset"
    liability = "liability"
    unclassified = "unclassified"


# Tally's "Income" primary groups. Sales Accounts is treated as income
# because Sales vouchers credit it.
INCOME_GROUPS: frozenset[str] = frozenset(
    {
        "direct incomes",
        "indirect incomes",
        "sales accounts",
    }
)

# Tally's "Expense" primary groups, including purchases.
EXPENSE_GROUPS: frozenset[str] = frozenset(
    {
        "direct expenses",
        "indirect expenses",
        "purchase accounts",
    }
)

# Asset-side groups on the balance sheet.
ASSET_GROUPS: frozenset[str] = frozenset(
    {
        "bank accounts",
        "bank od a/c",
        "cash-in-hand",
        "sundry debtors",
        "loans & advances (asset)",
        "current assets",
        "fixed assets",
        "investments",
        "stock-in-hand",
        "misc. expenses (asset)",
        "deposits (asset)",
    }
)

# Liability-side groups on the balance sheet.
LIABILITY_GROUPS: frozenset[str] = frozenset(
    {
        "sundry creditors",
        "current liabilities",
        "loans (liability)",
        "capital account",
        "reserves & surplus",
        "suspense a/c",
        "provisions",
        "duties & taxes",
        "branch / divisions",
        "secured loans",
        "unsecured loans",
    }
)


def _normalize(group_name: str | None) -> str:
    return (group_name or "").strip().lower()


def classify(group_name: str | None) -> GroupClass:
    """Return the report-side classification of a Tally group name.

    Unknown groups return `unclassified` so callers can decide whether
    to ignore the ledger or surface it as a warning. Phase 0 reports
    ignore unclassified groups (they don't affect totals).
    """
    n = _normalize(group_name)
    if not n:
        return GroupClass.unclassified
    if n in INCOME_GROUPS:
        return GroupClass.income
    if n in EXPENSE_GROUPS:
        return GroupClass.expense
    if n in ASSET_GROUPS:
        return GroupClass.asset
    if n in LIABILITY_GROUPS:
        return GroupClass.liability
    return GroupClass.unclassified


def is_income(group_name: str | None) -> bool:
    return classify(group_name) is GroupClass.income


def is_expense(group_name: str | None) -> bool:
    return classify(group_name) is GroupClass.expense


def is_asset(group_name: str | None) -> bool:
    return classify(group_name) is GroupClass.asset


def is_liability(group_name: str | None) -> bool:
    return classify(group_name) is GroupClass.liability


def is_sundry_debtors(group_name: str | None) -> bool:
    return _normalize(group_name) == "sundry debtors"


def is_sundry_creditors(group_name: str | None) -> bool:
    return _normalize(group_name) == "sundry creditors"
