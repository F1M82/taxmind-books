"""Unit tests for message_handlers dispatch."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from connector.message_handlers import (
    CompanyMismatch,
    _voucher_from_args,
    dispatch_command,
)
from connector.tally_client import (
    GroupMaster,
    LedgerMaster,
    OutstandingItem,
    TallyClient,
    TallyError,
    TallyUnreachable,
    TrialBalanceRow,
)


@pytest.fixture
def fake_tally() -> TallyClient:
    """A TallyClient with mocked async methods."""
    c = TallyClient(host="x", port=9000)
    c.ping = AsyncMock(return_value=True)  # type: ignore[method-assign]
    c.get_all_ledgers = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            LedgerMaster(name="L1", parent_group="G1", gstin=None),
        ]
    )
    c.get_all_groups = AsyncMock(  # type: ignore[method-assign]
        return_value=[GroupMaster(name="G1", parent="P1")]
    )
    c.get_trial_balance = AsyncMock(  # type: ignore[method-assign]
        return_value=[TrialBalanceRow(name="Cash", closing_balance=Decimal("100.00"))]
    )
    c.get_outstanding = AsyncMock(  # type: ignore[method-assign]
        return_value=[OutstandingItem(bill_name="INV-1", amount=Decimal("50.00"), due_date="20260415")]
    )
    c.post_voucher = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "success", "voucher_number": "R-1"}
    )
    return c


# ---------------- company mismatch ----------------


@pytest.mark.asyncio
async def test_dispatch_raises_company_mismatch(
    fake_tally: TallyClient,
) -> None:
    payload = {
        "company_id": "NOT-OURS",
        "command": "ping",
        "args": {},
    }
    with pytest.raises(CompanyMismatch):
        await dispatch_command(
            tally=fake_tally,
            payload=payload,
            registered_company_id="OURS",
        )


# ---------------- ping ----------------


@pytest.mark.asyncio
async def test_ping_returns_responsive(fake_tally: TallyClient) -> None:
    result = await dispatch_command(
        tally=fake_tally,
        payload={"command": "ping", "args": {}, "company_id": "C"},
        registered_company_id="C",
    )
    assert result["status"] == "success"
    assert result["result"]["tally_responsive"] is True


# ---------------- sync_masters ----------------


@pytest.mark.asyncio
async def test_sync_masters_collects_ledgers_and_groups(
    fake_tally: TallyClient,
) -> None:
    result = await dispatch_command(
        tally=fake_tally,
        payload={"command": "sync_masters", "args": {}, "company_id": "C"},
        registered_company_id="C",
    )
    assert result["status"] == "success"
    assert result["result"]["ledgers"][0]["name"] == "L1"
    assert result["result"]["groups"][0]["name"] == "G1"


# ---------------- post_voucher ----------------


@pytest.mark.asyncio
async def test_post_voucher_success(fake_tally: TallyClient) -> None:
    result = await dispatch_command(
        tally=fake_tally,
        payload={
            "command": "post_voucher",
            "company_id": "C",
            "args": {
                "voucher_type": "Receipt",
                "date": "2026-05-08",
                "voucher_number": "R-1",
                "party_name": "Sharma",
                "narration": "Payment",
                "entries": [
                    {"ledger_name": "Bank", "amount": "100.00", "entry_type": "Dr"},
                    {"ledger_name": "Sharma", "amount": "100.00", "entry_type": "Cr"},
                ],
            },
        },
        registered_company_id="C",
    )
    assert result["status"] == "success"
    assert result["result"]["voucher_number"] == "R-1"


@pytest.mark.asyncio
async def test_post_voucher_tally_unreachable_is_retryable(
    fake_tally: TallyClient,
) -> None:
    fake_tally.post_voucher = AsyncMock(  # type: ignore[method-assign]
        side_effect=TallyUnreachable("connection refused")
    )
    result = await dispatch_command(
        tally=fake_tally,
        payload={
            "command": "post_voucher",
            "company_id": "C",
            "args": {
                "voucher_type": "Receipt",
                "date": "2026-05-08",
                "voucher_number": "R-1",
                "party_name": "Sharma",
                "narration": "x",
                "entries": [
                    {"ledger_name": "Bank", "amount": "1.00", "entry_type": "Dr"},
                    {"ledger_name": "Sharma", "amount": "1.00", "entry_type": "Cr"},
                ],
            },
        },
        registered_company_id="C",
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "TallyUnreachable"
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_post_voucher_tally_error_subclass_not_retryable(
    fake_tally: TallyClient,
) -> None:
    class TallyValidationFailed(TallyError):
        pass

    fake_tally.post_voucher = AsyncMock(  # type: ignore[method-assign]
        side_effect=TallyValidationFailed("ledger not found")
    )
    result = await dispatch_command(
        tally=fake_tally,
        payload={
            "command": "post_voucher",
            "company_id": "C",
            "args": {
                "voucher_type": "Receipt",
                "date": "2026-05-08",
                "voucher_number": "R-1",
                "party_name": "Sharma",
                "narration": "x",
                "entries": [
                    {"ledger_name": "Bank", "amount": "1.00", "entry_type": "Dr"},
                    {"ledger_name": "Sharma", "amount": "1.00", "entry_type": "Cr"},
                ],
            },
        },
        registered_company_id="C",
    )
    assert result["status"] == "error"
    assert result["retryable"] is False


# ---------------- unknown command ----------------


@pytest.mark.asyncio
async def test_unknown_command_returns_unknown_command_error(
    fake_tally: TallyClient,
) -> None:
    result = await dispatch_command(
        tally=fake_tally,
        payload={"command": "frobnicate", "args": {}, "company_id": "C"},
        registered_company_id="C",
    )
    assert result["status"] == "error"
    assert result["error"]["code"] == "unknown_command"
    assert result["retryable"] is False


# ---------------- arg helpers ----------------


def test_voucher_from_args_decimal_amounts() -> None:
    v = _voucher_from_args(
        {
            "voucher_type": "Receipt",
            "date": "2026-05-08",
            "voucher_number": "R-1",
            "party_name": "Sharma",
            "narration": "x",
            "entries": [
                {
                    "ledger_name": "Bank",
                    "amount": "1500.00",
                    "entry_type": "Dr",
                },
                {
                    "ledger_name": "Sharma",
                    "amount": "1500.00",
                    "entry_type": "Cr",
                },
            ],
        }
    )
    assert v.voucher_date == date(2026, 5, 8)
    assert v.entries[0].amount == Decimal("1500.00")
    assert isinstance(v.entries[0].amount, Decimal)
    assert v.as_optional is False


def test_voucher_from_args_threads_as_optional_through() -> None:
    v = _voucher_from_args(
        {
            "voucher_type": "Sales",
            "date": "2026-05-08",
            "voucher_number": "S-1",
            "party_name": "Acme",
            "narration": "AI",
            "as_optional": True,
            "entries": [
                {"ledger_name": "Acme", "amount": "100", "entry_type": "Dr"},
                {"ledger_name": "Sales", "amount": "100", "entry_type": "Cr"},
            ],
        }
    )
    assert v.as_optional is True


# ---------------- Optional voucher commands (v1.2) ----------------


@pytest.mark.asyncio
async def test_approve_optional_voucher_dispatches_to_tally(
    fake_tally: TallyClient,
) -> None:
    fake_tally.approve_optional_voucher = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "success",
            "tally_voucher_guid": "GUID-1",
            "raw": "<OK/>",
        }
    )
    result = await dispatch_command(
        tally=fake_tally,
        payload={
            "command": "approve_optional_voucher",
            "company_id": "C",
            "args": {"tally_voucher_guid": "GUID-1"},
        },
        registered_company_id="C",
    )
    assert result["status"] == "success"
    assert result["result"]["tally_voucher_guid"] == "GUID-1"
    fake_tally.approve_optional_voucher.assert_awaited_once_with("GUID-1")


@pytest.mark.asyncio
async def test_reject_optional_voucher_dispatches_to_tally(
    fake_tally: TallyClient,
) -> None:
    fake_tally.reject_optional_voucher = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "success",
            "tally_voucher_guid": "GUID-2",
            "raw": "<OK/>",
        }
    )
    result = await dispatch_command(
        tally=fake_tally,
        payload={
            "command": "reject_optional_voucher",
            "company_id": "C",
            "args": {"tally_voucher_guid": "GUID-2"},
        },
        registered_company_id="C",
    )
    assert result["status"] == "success"
    assert result["result"]["tally_voucher_guid"] == "GUID-2"
    fake_tally.reject_optional_voucher.assert_awaited_once_with("GUID-2")
