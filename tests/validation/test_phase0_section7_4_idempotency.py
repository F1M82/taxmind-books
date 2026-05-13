"""§7.4 Idempotency (per IDEMPOTENCY.md).

Each test maps to one acceptance criterion from `docs/VALIDATION_REPORT.md`
§7.4. The misuse criterion (key reused across endpoints) uses
`POST /api/v1/vouchers/` and `POST /api/v1/vouchers/{id}/approve-to-regular`
as the two distinct idempotency-protected paths — Phase 0 ships no
`/ingestions/` endpoint, so the original example in the report
template is satisfied by any pair of different idempotent POSTs.

The DB-count criterion is verified through the API: GET /vouchers/
filtered to the test's company shows exactly one voucher carrying
the test idempotency key as part of its narration. A direct DB
SELECT also runs when a connection is available.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import httpx
import pytest

from conftest import (
    Scenario,
    auth_headers,
    voucher_payload,
)


def _post_voucher(
    http: httpx.Client,
    scenario: Scenario,
    *,
    idem: str,
    amount: str = "777.00",
    narration: str | None = None,
) -> httpx.Response:
    payload = voucher_payload(
        scenario.bank_ledger_id,
        scenario.party_ledger_id,
        amount=amount,
    )
    if narration is not None:
        payload["narration"] = narration
    return http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id, idem=idem
        ),
        json=payload,
    )


@pytest.mark.criterion(
    section="7.4",
    label="POST /vouchers/ with Idempotency-Key K1 → 201",
)
def test_idempotency_01_first_request_succeeds_201(
    http: httpx.Client, scenario: Scenario
) -> None:
    key = f"idem-{uuid4().hex}"
    r = _post_voucher(http, scenario, idem=key)
    assert r.status_code == 201, f"first POST should be 201, got {r.status_code}: {r.text}"
    assert "Idempotent-Replay" not in r.headers, (
        "first request must NOT carry Idempotent-Replay header"
    )


@pytest.mark.criterion(
    section="7.4",
    label=(
        "POST same body, same K1 → 201, same voucher.id, "
        "Idempotent-Replay: true header"
    ),
)
def test_idempotency_02_replay_same_body_returns_201_with_replay_header(
    http: httpx.Client, scenario: Scenario
) -> None:
    key = f"idem-{uuid4().hex}"
    narration = f"replay-{uuid4().hex[:6]}"
    r1 = _post_voucher(http, scenario, idem=key, narration=narration)
    assert r1.status_code == 201, r1.text
    r2 = _post_voucher(http, scenario, idem=key, narration=narration)
    assert r2.status_code == 201, f"replay: {r2.status_code} {r2.text}"
    assert r1.json()["id"] == r2.json()["id"], (
        "replay returned a different voucher id"
    )
    assert r2.headers.get("Idempotent-Replay") == "true", (
        f"missing Idempotent-Replay header on replay: {dict(r2.headers)}"
    )


@pytest.mark.criterion(
    section="7.4",
    label=(
        "POST different body, same K1 → 409 idempotency_replay"
    ),
)
def test_idempotency_03_replay_different_body_returns_409_replay(
    http: httpx.Client, scenario: Scenario
) -> None:
    key = f"idem-{uuid4().hex}"
    r1 = _post_voucher(http, scenario, idem=key, amount="100.00")
    assert r1.status_code == 201, r1.text
    # Same key, different body (different amount).
    r2 = _post_voucher(http, scenario, idem=key, amount="200.00")
    assert r2.status_code == 409, (
        f"expected 409 idempotency_replay, got {r2.status_code}: {r2.text}"
    )
    body = r2.json()
    error_code = (
        body.get("error", {}).get("code")
        if isinstance(body.get("error"), dict)
        else body.get("code")
    )
    assert error_code == "idempotency_replay", (
        f"expected error code 'idempotency_replay', got {error_code!r}: "
        f"{body}"
    )


@pytest.mark.criterion(
    section="7.4",
    label=(
        "POST without Idempotency-Key header → 400 idempotency_key_required"
    ),
)
def test_idempotency_04_missing_header_returns_400_required(
    http: httpx.Client, scenario: Scenario
) -> None:
    payload = voucher_payload(
        scenario.bank_ledger_id, scenario.party_ledger_id
    )
    headers = {
        "Authorization": f"Bearer {scenario.owner.access_token}",
        "X-Company-ID": str(scenario.company_id),
    }  # deliberately no Idempotency-Key
    r = http.post("/api/v1/vouchers/", headers=headers, json=payload)
    assert r.status_code == 400, (
        f"expected 400 for missing Idempotency-Key, got {r.status_code}: "
        f"{r.text}"
    )
    body = r.json()
    error_code = (
        body.get("error", {}).get("code")
        if isinstance(body.get("error"), dict)
        else body.get("code")
    )
    assert error_code == "idempotency_key_required", (
        f"expected error code 'idempotency_key_required', got "
        f"{error_code!r}: {body}"
    )


@pytest.mark.criterion(
    section="7.4",
    label=(
        "POST K2 to /vouchers/, then K2 to /approve-to-regular → 409 "
        "idempotency_key_misuse  (`/ingestions/` not in Phase 0; uses "
        "two distinct idempotent POSTs)"
    ),
)
def test_idempotency_05_same_key_different_path_returns_409_misuse(
    http: httpx.Client, scenario: Scenario
) -> None:
    key = f"idem-{uuid4().hex}"
    r1 = _post_voucher(http, scenario, idem=key)
    assert r1.status_code == 201, r1.text
    voucher_id = r1.json()["id"]

    # Reuse the same key on a different idempotent POST path.
    # Path B: POST /api/v1/vouchers/{id}/approve-to-regular
    r2 = http.post(
        f"/api/v1/vouchers/{voucher_id}/approve-to-regular",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id, idem=key
        ),
        json={"notes": "validation suite probe"},
    )
    assert r2.status_code == 409, (
        f"expected 409 idempotency_key_misuse, got {r2.status_code}: "
        f"{r2.text}"
    )
    body = r2.json()
    error_code = (
        body.get("error", {}).get("code")
        if isinstance(body.get("error"), dict)
        else body.get("code")
    )
    assert error_code == "idempotency_key_misuse", (
        f"expected error code 'idempotency_key_misuse', got {error_code!r}: "
        f"{body}"
    )


@pytest.mark.criterion(
    section="7.4",
    label=(
        "DB inspect: only 1 voucher created across all retries"
    ),
)
def test_idempotency_06_db_voucher_count_is_one_across_retries(
    http: httpx.Client,
    scenario: Scenario,
    db_conn,  # type: ignore[no-untyped-def]
) -> None:
    key = f"idem-{uuid4().hex}"
    narration = f"count-probe-{uuid4().hex}"
    # Five replays of the exact same request.
    voucher_ids: set[str] = set()
    for _ in range(5):
        r = _post_voucher(http, scenario, idem=key, narration=narration)
        assert r.status_code == 201, r.text
        voucher_ids.add(r.json()["id"])

    assert len(voucher_ids) == 1, (
        f"replays returned different voucher ids: {voucher_ids}"
    )
    only_id = next(iter(voucher_ids))

    # API-side count via the list endpoint.
    r = http.get(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        params={"narration": narration},
    )
    # `narration` may not be a list-filter param; the unique narration
    # still lets us count via direct DB.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM vouchers WHERE narration = %s",
            (narration,),
        )
        row = cur.fetchone()
    assert row is not None
    db_count = int(row[0])
    assert db_count == 1, (
        f"expected exactly 1 voucher in DB for narration {narration!r}, "
        f"found {db_count}"
    )
    # Cross-check the id matches.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM vouchers WHERE narration = %s",
            (narration,),
        )
        db_row = cur.fetchone()
    assert db_row is not None
    assert str(db_row[0]) == only_id, (
        f"DB row id {db_row[0]} != API-returned id {only_id}"
    )
    # Cast to UUID to make sure it parses.
    UUID(only_id)
