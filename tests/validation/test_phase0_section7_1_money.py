"""§7.1 Money handling (per MONEY.md).

Each test maps to one acceptance criterion from `docs/VALIDATION_REPORT.md`
§7.1 and carries a `@pytest.mark.criterion(...)` so the end-of-session
checklist can render the report-shaped output.

The fourth criterion ("DB shows exact value, no float artifact") is
verified through the API round-trip: the API serializes `Money` as a
string with `PlainSerializer(..., when_used="json")`. If a float
artifact had snuck in, the response would carry it back as a
non-exact string (e.g. `"1500.9899999999..."` instead of `"1500.99"`).
We additionally cross-check via direct DB SELECT when a connection is
available.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from conftest import (
    Scenario,
    auth_headers,
    create_voucher,
    voucher_payload,
)


@pytest.mark.criterion(
    section="7.1",
    label=(
        "POST /vouchers/ with `total_amount: 1500.99` (float in JSON) "
        "→ expected 422"
    ),
)
def test_money_01_float_in_json_rejected_with_422(
    http: httpx.Client, scenario: Scenario
) -> None:
    payload = voucher_payload(
        scenario.bank_ledger_id,
        scenario.party_ledger_id,
        amount="1500.99",
    )
    # Replace the string with an actual JSON number — this is the
    # exact failure mode MONEY.md is designed to reject.
    payload["total_amount"] = 1500.99  # type: ignore[assignment]
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner,
            company_id=scenario.company_id,
            idem=uuid4().hex,
        ),
        json=payload,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


@pytest.mark.criterion(
    section="7.1",
    label=(
        "POST /vouchers/ with `\"total_amount\": \"1500.99\"` (string) "
        "→ expected 201, response has string"
    ),
)
def test_money_02_string_amount_accepted_and_echoed_as_string(
    http: httpx.Client, scenario: Scenario
) -> None:
    body = create_voucher(http, scenario, amount="1500.99")
    assert body["total_amount"] == "1500.99", (
        f"expected string '1500.99', got {body['total_amount']!r} "
        f"(type={type(body['total_amount']).__name__})"
    )
    # Cross-check Decimal equality so a subtle re-serialization that
    # preserves '1500.99' string but encodes a different value would
    # also fail this test.
    assert Decimal(body["total_amount"]) == Decimal("1500.99")


@pytest.mark.criterion(
    section="7.1",
    label=(
        "POST /vouchers/ with `\"total_amount\": \"1500.999\"` (3 dp) "
        "→ expected 422"
    ),
)
def test_money_03_three_decimal_places_rejected_with_422(
    http: httpx.Client, scenario: Scenario
) -> None:
    payload = voucher_payload(
        scenario.bank_ledger_id,
        scenario.party_ledger_id,
        amount="1500.999",  # 3 dp — must violate decimal_places=2
    )
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner,
            company_id=scenario.company_id,
            idem=uuid4().hex,
        ),
        json=payload,
    )
    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"


@pytest.mark.criterion(
    section="7.1",
    label=(
        "DB inspect: `SELECT total_amount FROM vouchers LIMIT 1` shows "
        "exact value, no float artifact"
    ),
)
def test_money_04_db_value_preserved_no_float_artifact(
    http: httpx.Client, scenario: Scenario, db_conn  # type: ignore[no-untyped-def]
) -> None:
    body = create_voucher(http, scenario, amount="1500.99")
    voucher_id = body["id"]

    # 1) API round-trip: GET the same voucher and confirm the string
    #    is byte-identical to what we posted.
    r = http.get(
        f"/api/v1/vouchers/{voucher_id}",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["total_amount"] == "1500.99"

    # 2) Direct DB read: numeric column must hold exact 1500.99 with
    #    scale 2, with no float drift.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT total_amount FROM vouchers WHERE id = %s",
            (voucher_id,),
        )
        row = cur.fetchone()
    assert row is not None, "voucher not found in DB"
    db_value = row[0]
    assert isinstance(db_value, Decimal), (
        f"DB driver returned {type(db_value).__name__}, expected Decimal"
    )
    assert db_value == Decimal("1500.99"), (
        f"DB value {db_value!r} != Decimal('1500.99') — float artifact"
    )
    # The string form pins the column's scale (no trailing-zero
    # truncation, no extra precision creep).
    assert str(db_value) == "1500.99", (
        f"DB value rendered as {str(db_value)!r}; expected '1500.99'"
    )
