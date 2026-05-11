"""Tally-group classifications used by per-voucher-type validation.

These constants mirror Tally's primary group names. They are deliberately
minimal — only the groups that participate in voucher-type business rules
per `docs/API.md` § "POST /vouchers/" → Validation rules. The richer
classification used by reports lives in
`backend/app/services/reporting/tally_groups.py` (P0.38).

Comparison is case-insensitive on the canonical Tally spelling.
"""

from __future__ import annotations

# Bank and Cash groups — the "treasury" side. Receipt vouchers receive
# into one of these; Payment vouchers pay from one; Contra is a transfer
# between them (all entries must be Bank/Cash).
BANK_OR_CASH: frozenset[str] = frozenset(
    {"bank accounts", "bank od a/c", "cash-in-hand"}
)

SUNDRY_DEBTORS: frozenset[str] = frozenset({"sundry debtors"})
SUNDRY_CREDITORS: frozenset[str] = frozenset({"sundry creditors"})


def is_bank_or_cash(group_name: str | None) -> bool:
    return group_name is not None and group_name.strip().lower() in BANK_OR_CASH


def is_sundry_debtors(group_name: str | None) -> bool:
    return (
        group_name is not None and group_name.strip().lower() in SUNDRY_DEBTORS
    )


def is_sundry_creditors(group_name: str | None) -> bool:
    return (
        group_name is not None
        and group_name.strip().lower() in SUNDRY_CREDITORS
    )
