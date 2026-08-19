"""Dispatch backend `command` messages to the local TallyClient.

Each backend command (`ping`, `sync_masters`, `post_voucher`,
`get_trial_balance`, `get_outstanding`, `export_vouchers`,
`approve_optional_voucher`, `reject_optional_voucher`) maps to one
handler that reads
`payload.args`, calls the appropriate `TallyClient` method, and
returns a `result` dict for inclusion in the `command_result`
reply envelope.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date as _date
from decimal import Decimal
from typing import Any

from connector.idempotency_cache import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_FLIGHT,
    IdempotencyCache,
    hash_request_payload,
)
from connector.tally_client import (
    LedgerEntryInput,
    TallyClient,
    TallyError,
    VoucherExportRow,
    VoucherInput,
    WrongCompanyOpen,
)
from connector.tally_data_folder import TallyDataFolderError

logger = logging.getLogger("connector.message_handlers")

# Per docs/connector_idempotency_design.md (DECISION 4): only these
# mutating commands consult the idempotency cache. Read-only commands
# (ping, sync_masters, get_*) bypass it — re-execution is harmless and
# returns fresh data.
MUTATING_COMMANDS = frozenset(
    {
        "post_voucher",
        "approve_optional_voucher",
        "reject_optional_voucher",
    }
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
    """Pull all ledgers + groups + the Tally company identity.

    The company identity (name + GUID) is captured alongside the masters so
    the backend's persistence gate can prove the ledgers belong to the mapped
    Tally company (Phase 7B). Phase-0: full pull, no `since` delta.
    """
    ledgers = await tally.get_all_ledgers()
    groups = await tally.get_all_groups()
    company = await tally.get_company_info()
    return {
        "company": {"name": company.name, "guid": company.guid},
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


async def _handle_company_info(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    """READ-ONLY Tally company identity discovery (Phase 7B).

    Returns the current Tally company's display name and durable GUID as
    separate fields — the GUID is never inferred from the name.
    """
    company = await tally.get_company_info()
    return {"name": company.name, "guid": company.guid}


async def _handle_list_tally_companies(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    try:
        companies = tally.list_tally_companies()
    except TallyDataFolderError as exc:
        return {
            "_error": {
                "code": "data_folder_unreadable",
                "message": str(exc),
                "details": {"path": tally.data_folder_path},
            }
        }
    return {
        "tally_data_folder_path": tally.data_folder_path,
        "companies": [company.as_dict() for company in companies],
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


async def _handle_get_active_tally_company(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    return await tally.get_active_tally_company()


async def _handle_post_voucher(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    target = args.get("target_tally_company_identifier")
    if target is not None:
        active = await tally.get_active_tally_company()
        if active.get("tally_company_identifier") != target:
            raise WrongCompanyOpen(
                "requested company is not currently open in Tally"
            )
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


def _is_yyyymmdd(value: object) -> bool:
    return isinstance(value, str) and len(value) == 8 and value.isdigit()


def _export_row_to_dict(r: VoucherExportRow) -> dict[str, Any]:
    """Serialise a VoucherExportRow for the command_result envelope."""
    return {
        "tally_guid": r.tally_guid,
        "remote_id": r.remote_id,
        "vchkey": r.vchkey,
        "master_id": r.master_id,
        "alter_id": r.alter_id,
        "voucher_key": r.voucher_key,
        "voucher_type": r.voucher_type,
        "date": r.date.isoformat(),
        "voucher_number": r.voucher_number,
        "narration": r.narration,
        "reference": r.reference,
        "party_ledger_name": r.party_ledger_name,
        "is_cancelled": r.is_cancelled,
        "is_optional": r.is_optional,
        "is_deleted": r.is_deleted,
        "entries": [
            {
                "ledger_name": e.ledger_name,
                "ledger_guid": e.ledger_guid,
                "amount": str(e.amount),
                "entry_type": e.entry_type,
            }
            for e in r.entries
        ],
    }


async def _handle_export_vouchers(
    tally: TallyClient, args: dict[str, Any]
) -> dict[str, Any]:
    """READ-ONLY historical voucher export (P3.1).

    Args: ``from_date`` / ``to_date`` as Tally ``YYYYMMDD`` strings. Delegates
    to `TallyClient.get_vouchers`, which issues only an Export request — this
    command can never write to, alter, or delete anything in Tally. It is
    deliberately absent from `MUTATING_COMMANDS`, so it bypasses the
    idempotency cache: re-running is side-effect-free and returns fresh data.
    """
    from_date = args.get("from_date")
    to_date = args.get("to_date")
    if not _is_yyyymmdd(from_date):
        raise ValueError(
            "export_vouchers.args.from_date must be a YYYYMMDD string"
        )
    if not _is_yyyymmdd(to_date):
        raise ValueError(
            "export_vouchers.args.to_date must be a YYYYMMDD string"
        )
    rows = await tally.get_vouchers(from_date, to_date)  # type: ignore[arg-type]
    # Enrich each ledger line with the ledger's native GUID (P3.7 Phase 6A):
    # the voucher XML carries only LEDGERNAME; the GUID comes from the ledger
    # master list (verified <GUID> field) via an exact-name join.
    ledgers = await tally.get_all_ledgers()
    rows = tally.enrich_ledger_guids(rows, ledgers)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "count": len(rows),
        "vouchers": [_export_row_to_dict(r) for r in rows],
    }


HANDLERS: dict[str, HandlerFn] = {
    "ping": _handle_ping,
    "sync_masters": _handle_sync_masters,
    "company_info": _handle_company_info,
    "list_tally_companies": _handle_list_tally_companies,
    "get_active_tally_company": _handle_get_active_tally_company,
    "post_voucher": _handle_post_voucher,
    "get_trial_balance": _handle_get_trial_balance,
    "get_outstanding": _handle_get_outstanding,
    "export_vouchers": _handle_export_vouchers,
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
    authorized_company_ids: set[str] | None = None,
    cache: IdempotencyCache | None = None,
) -> dict[str, Any]:
    """Run the command in `payload.command` and build the
    `command_result.payload` (minus `request_id` / `ts`).

    When `cache` is provided and the command is mutating (`post_voucher`,
    `approve_optional_voucher`, `reject_optional_voucher`), the call is
    deduplicated on `payload.idempotency_key`: a previously-completed or
    non-retryably-failed key returns its cached result without touching
    Tally. Non-mutating commands, calls without a cache, and calls without
    a usable key execute directly as before. See
    docs/connector_idempotency_design.md.

    Raises:
        CompanyMismatch: if `payload.company_id` is not authorized by the
            server registration. Empty authorization lists preserve the
            legacy exact-company check.
    """
    started = time.monotonic()
    company_id = payload.get("company_id")
    authorized = authorized_company_ids or {registered_company_id}
    if company_id is not None and str(company_id) not in authorized:
        raise CompanyMismatch(
            f"command targets {company_id!r}, connector is not authorized "
            f"for that company"
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
    # A handler is only found for a str command, so command is a str here.
    assert isinstance(command, str)

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

    idempotency_key = payload.get("idempotency_key")
    if (
        cache is not None
        and command in MUTATING_COMMANDS
        and isinstance(idempotency_key, str)
        and idempotency_key
    ):
        return await _dispatch_mutating_with_cache(
            tally=tally,
            cache=cache,
            handler=handler,
            command=command,
            args=args,
            idempotency_key=idempotency_key,
            started=started,
        )

    return await _run_handler(handler, tally, args, command, started)


async def _run_handler(
    handler: HandlerFn,
    tally: TallyClient,
    args: dict[str, Any],
    command: str,
    started: float,
) -> dict[str, Any]:
    """Execute one handler and build its `command_result` payload.

    `retryable` is True for transport / ambiguous Tally failures and
    False for structural rejections — the classification BUG-004 Layer A
    established. The idempotency layer keys its terminal decision off this
    same flag.
    """
    try:
        result = await handler(tally, args)
    except TallyError as exc:
        error_code = {
            "WrongCompanyOpen": "wrong_company_open",
        }.get(exc.__class__.__name__, exc.__class__.__name__)
        return {
            "command": command,
            "status": "error",
            "error": {
                "code": error_code,
                "message": str(exc),
            },
            "duration_ms": _ms_since(started),
            "retryable": exc.__class__.__name__
            in {
                "TallyUnreachable",
                "TallyResponseError",
                "TallyAmbiguousResponse",
                "WrongCompanyOpen",
            },
        }

    if "_error" in result:
        error = result["_error"]
        return {
            "command": command,
            "status": "error",
            "error": error,
            "duration_ms": _ms_since(started),
            "retryable": False,
        }

    return {
        "command": command,
        "status": "success",
        "result": result,
        "duration_ms": _ms_since(started),
    }


async def _dispatch_mutating_with_cache(
    *,
    tally: TallyClient,
    cache: IdempotencyCache,
    handler: HandlerFn,
    command: str,
    args: dict[str, Any],
    idempotency_key: str,
    started: float,
) -> dict[str, Any]:
    """Execute a mutating command under the idempotency cache.

    Lifecycle (docs/connector_idempotency_design.md §"Lifecycle"):
      - completed  → return the cached result, no Tally call
      - failed     → return the cached (non-retryable) error, no Tally call
      - in_flight  → crash window (OPEN-A): WARN and proceed as first-seen
      - absent     → record in_flight, execute, settle terminal/retry

    The terminal decision is driven by the result envelope's `retryable`
    flag: a non-retryable error is cached as `failed`; a retryable one
    deletes the in_flight row so the next attempt re-executes fresh.

    Cache methods are synchronous (`sqlite3`); each is dispatched via
    `asyncio.to_thread` so this coroutine never blocks the event loop.
    """
    request_hash = hash_request_payload(args)
    existing = await asyncio.to_thread(cache.get, command, idempotency_key)

    if existing is not None and existing.status == STATUS_COMPLETED:
        return existing.result_payload or {}
    if existing is not None and existing.status == STATUS_FAILED:
        return existing.result_payload or {}
    if existing is not None and existing.status == STATUS_IN_FLIGHT:
        # OPEN-A: a prior attempt recorded intent but never settled —
        # almost certainly a connector crash between the Tally post and
        # the terminal write. We cannot know whether Tally accepted the
        # post, so per the session-open decision we proceed as first-seen,
        # accepting the bounded duplicate-post risk (manual reconciliation
        # is the documented operator action). We do NOT re-insert the
        # in_flight row (it already exists); the terminal settle below
        # upserts it.
        # TODO(OPEN-A): close this window via REMOTEID-on-Create / Tally
        # reconciliation once BUG-004 Layer C lands.
        logger.warning(
            "idempotency: in-flight row for %s key=%s never settled "
            "(possible crash); proceeding as first-seen, duplicate-post "
            "risk accepted (OPEN-A)",
            command,
            idempotency_key,
        )
    else:
        await asyncio.to_thread(
            cache.record_in_flight, command, idempotency_key, request_hash
        )

    envelope = await _run_handler(handler, tally, args, command, started)

    # Cache decisions are driven off the envelope's `retryable` flag (set
    # from TallyError.retryable) per the session-2 decision, NOT off the
    # exception class. This depends on the BUG-004 Layer A four-class
    # hierarchy: TallyImportRejected/company_mismatch → non-retryable
    # (cache as failed); TallyUnreachable/TallyResponseError/
    # TallyAmbiguousResponse → retryable (delete, never cache). If that
    # hierarchy or its retryable classification changes, revisit this.
    if envelope.get("status") == "success":
        await asyncio.to_thread(
            cache.record_completed, command, idempotency_key, envelope
        )
    elif envelope.get("retryable"):
        await asyncio.to_thread(
            cache.delete_for_retry, command, idempotency_key
        )
    else:
        await asyncio.to_thread(
            cache.record_failed, command, idempotency_key, envelope
        )

    return envelope


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

    # BUG-004 Layer C: the backend sends its voucher id so we can stamp it
    # as the Tally REMOTEID on Create. Absent → no REMOTEID (legacy path).
    rid = args.get("voucher_id")
    remote_id = str(rid) if rid else None

    return VoucherInput(
        voucher_type=str(args.get("voucher_type", "Receipt")),
        voucher_date=voucher_date,
        voucher_number=str(args.get("voucher_number", "")),
        party_name=str(args.get("party_name", "")),
        narration=str(args.get("narration", "")),
        entries=entries,
        as_optional=bool(args.get("as_optional", False)),
        remote_id=remote_id,
    )
