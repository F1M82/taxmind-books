"""Dispatch backend `command` messages to the local TallyClient.

Each backend command (`ping`, `sync_masters`, `post_voucher`,
`get_trial_balance`, `get_outstanding`, `approve_optional_voucher`,
`reject_optional_voucher`) maps to one handler that reads
`payload.args`, calls the appropriate `TallyClient` method, and
returns a `result` dict for inclusion in the `command_result`
reply envelope.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import date as _date
from decimal import Decimal
from typing import Any

from connector.tally_client import (
    LedgerEntryInput,
    TallyClient,
    TallyError,
    VoucherInput,
)


class CompanyMismatch(Exception):
    """Backend command's company_id ≠ the connector's registered company."""


HandlerFn = Callable[[TallyClient, dict[str, Any]], Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------


async def _handle_ping(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    ok = await tally.ping()
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "tally_responsive": ok,
        "ping_duration_ms": duration_ms,
    }


async def _handle_sync_masters(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    """Pull all ledgers + groups. Phase-0: full pull, no `since` delta."""
    ledgers = await tally.get_all_ledgers()
    groups = await tally.get_all_groups()
    return {
        "ledgers": [
            {
                "name": led.name,
                "group_name": led.parent_group,
                "gstin": led.gstin,
                "master_id": led.master_id,
            }
            for led in ledgers
        ],
        "groups": [
            {"name": g.name, "parent": g.parent} for g in groups
        ],
    }


async def _handle_post_voucher(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    voucher = _voucher_from_args(args)
    return await tally.post_voucher(voucher)


async def _handle_get_trial_balance(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    rows = await tally.get_trial_balance(
        from_date=args.get("from_date"),
        to_date=args.get("to_date"),
    )
    return {
        "rows": [
            {"name": r.name, "closing_balance": str(r.closing_balance)}
            for r in rows
        ]
    }


async def _handle_get_outstanding(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    party_type = args.get("party_type", "Sundry Debtors")
    items = await tally.get_outstanding(
        party_type=party_type,
        as_of_date=args.get("as_of_date"),
    )
    return {
        "items": [
            {
                "bill_name": i.bill_name,
                "amount": str(i.amount),
                "due_date": i.due_date,
            }
            for i in items
        ]
    }


async def _handle_approve_optional_voucher(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    guid = args.get("tally_voucher_guid")
    if not isinstance(guid, str) or not guid:
        raise ValueError(
            "approve_optional_voucher.args.tally_voucher_guid is required"
        )
    return await tally.approve_optional_voucher(guid)


async def _handle_reject_optional_voucher(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    guid = args.get("tally_voucher_guid")
    if not isinstance(guid, str) or not guid:
        raise ValueError(
            "reject_optional_voucher.args.tally_voucher_guid is required"
        )
    return await tally.reject_optional_voucher(guid)


HANDLERS: dict[str, HandlerFn] = {
    "ping": _handle_ping,
    "sync_masters": _handle_sync_masters,
    "post_voucher": _handle_post_voucher,
    "get_trial_balance": _handle_get_trial_balance,
    "get_outstanding": _handle_get_outstanding,
    "approve_optional_voucher": _handle_approve_optional_voucher,
    "reject_optional_voucher": _handle_reject_optional_voucher,
}


# ---------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------


async def dispatch_command(
    *,
    tally: TallyClient,
    payload: dict[str, Any],
    registered_company_id: str,
) -> dict[str, Any]:
    """Run the command in `payload.command` and build the
    `command_result.payload` (minus `request_id` / `ts`).

    Raises:
        CompanyMismatch: if `payload.company_id` ≠ the connector's
            registered company. The dispatcher forwards this as a
            `company_mismatch` error in the reply.
    """
    started = time.monotonic()
    company_id = payload.get("company_id")
    if company_id is not None and str(company_id) != registered_company_id:
        raise CompanyMismatch(
            f"command targets {company_id!r}, connector registered for "
            f"{registered_company_id!r}"
        )

    command = payload.get("command")
    handler = HANDLERS.get(command) if isinstance(command, str) else None
    if handler is None:
        return {
            "command": command,
            "status": "error",
            "error": {
                "code": "unknown_command",
                "message": f"Connector does not implement command {command!r}",
            },
            "duration_ms": _ms_since(started),
            "retryable": False,
        }

    args = payload.get("args") or {}
    if not isinstance(args, dict):
        return {
            "command": command,
            "status": "error",
            "error": {
                "code": "invalid_args",
                "message": "command.args must be an object",
            },
            "duration_ms": _ms_since(started),
            "retryable": False,
        }

    try:
        result = await handler(tally, args)
    except TallyError as exc:
        return {
            "command": command,
            "status": "error",
            "error": {
                "code": exc.__class__.__name__,
                "message": str(exc),
            },
            "duration_ms": _ms_since(started),
            "retryable": exc.__class__.__name__ in {
                "TallyUnreachable",
                "TallyResponseError",
                "TallyAmbiguousResponse",
            },
        }

    return {
        "command": command,
        "status": "success",
        "result": result,
        "duration_ms": _ms_since(started),
    }


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _voucher_from_args(args: dict[str, Any]) -> VoucherInput:
    """Build a VoucherInput from a backend post_voucher command's args.

    Schema is the Phase-0 manual voucher: voucher_type, date,
    voucher_number (may be empty), party_name, narration, entries[].
    Each entry has ledger_name, amount (string), entry_type.
    """
    raw_date = args.get("date")
    if isinstance(raw_date, str):
        voucher_date = _date.fromisoformat(raw_date)
    elif isinstance(raw_date, _date):
        voucher_date = raw_date
    else:
        raise ValueError("post_voucher.args.date must be ISO-8601 date string")

    entries_raw = args.get("entries", [])
    if not isinstance(entries_raw, list):
        raise ValueError("post_voucher.args.entries must be an array")

    entries = [
        LedgerEntryInput(
            ledger_name=str(e["ledger_name"]),
            amount=Decimal(str(e["amount"])),
            entry_type=str(e["entry_type"]),
        )
        for e in entries_raw
    ]

    return VoucherInput(
        voucher_type=str(args.get("voucher_type", "Receipt")),
        voucher_date=voucher_date,
        voucher_number=str(args.get("voucher_number", "")),
        party_name=str(args.get("party_name", "")),
        narration=str(args.get("narration", "")),
        entries=entries,
        as_optional=bool(args.get("as_optional", False)),
    )
