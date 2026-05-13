"""§7.3 Audit log (per AUDIT.md).

Each test maps to one acceptance criterion from `docs/VALIDATION_REPORT.md`
§7.3. Reads go through `GET /api/v1/audit-logs/`; the append-only
trigger check writes directly via psycopg because no API path
exposes raw UPDATE on `audit_logs`.

The "no secret literal" sweep walks the JSON of every audit row the
suite generates and asserts no plaintext password/token/api_key
appears verbatim. The redactor in `app/core/audit.py` replaces those
keys with `***REDACTED***` before the row is persisted.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import (
    Scenario,
    add_member,
    auth_headers,
    create_voucher,
    register_and_login,
)


def _list_audit_rows_for_voucher(
    http: httpx.Client, scenario: Scenario, voucher_id: str
) -> list[dict]:
    r = http.get(
        "/api/v1/audit-logs/",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        params={"entity_type": "voucher", "entity_id": voucher_id},
    )
    assert r.status_code == 200, r.text
    # API returns newest first; reverse so [0] is voucher.created.
    return list(reversed(r.json()["items"]))


@pytest.mark.criterion(
    section="7.3",
    label=(
        "Create voucher; query audit_logs → exactly 1 row, "
        "action='voucher.created', new_value contains total_amount as "
        "string"
    ),
)
def test_audit_01_voucher_create_emits_one_row_with_total_amount_as_string(
    http: httpx.Client, scenario: Scenario
) -> None:
    voucher = create_voucher(http, scenario, amount="1500.99")
    rows = _list_audit_rows_for_voucher(http, scenario, voucher["id"])
    assert len(rows) == 1, (
        f"expected exactly 1 audit row after create, got {len(rows)}: "
        f"{[r['action'] for r in rows]}"
    )
    assert rows[0]["action"] == "voucher.created"
    new_value = rows[0]["new_value"] or {}
    assert "total_amount" in new_value, (
        f"new_value missing total_amount: keys={list(new_value)}"
    )
    raw = new_value["total_amount"]
    assert isinstance(raw, str), (
        f"total_amount in audit must be a string, got {type(raw).__name__}: "
        f"{raw!r}"
    )
    assert Decimal(raw) == Decimal("1500.99")


@pytest.mark.criterion(
    section="7.3",
    label=(
        "Update narration; query audit_logs → 2nd row, changes contains "
        "only narration"
    ),
)
def test_audit_02_voucher_update_emits_row_with_only_changed_field(
    http: httpx.Client, scenario: Scenario
) -> None:
    voucher = create_voucher(http, scenario, narration="original narration")
    r = http.patch(
        f"/api/v1/vouchers/{voucher['id']}",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        json={"narration": "updated narration"},
    )
    assert r.status_code == 200, r.text

    rows = _list_audit_rows_for_voucher(http, scenario, voucher["id"])
    assert len(rows) == 2, (
        f"expected 2 audit rows after update, got {len(rows)}: "
        f"{[x['action'] for x in rows]}"
    )
    update_row = rows[1]
    assert update_row["action"] == "voucher.updated", update_row["action"]
    changes = update_row["changes"] or {}
    assert set(changes.keys()) == {"narration"}, (
        f"changes must contain only 'narration', got {list(changes)}"
    )


@pytest.mark.criterion(
    section="7.3",
    label=(
        "Cancel voucher; 3rd row with action='voucher.cancelled'"
    ),
)
def test_audit_03_voucher_cancel_emits_row_with_action_cancelled(
    http: httpx.Client, scenario: Scenario
) -> None:
    voucher = create_voucher(http, scenario)
    r = http.patch(
        f"/api/v1/vouchers/{voucher['id']}",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        json={"narration": "pre-cancel update"},
    )
    assert r.status_code == 200, r.text
    r = http.post(
        f"/api/v1/vouchers/{voucher['id']}/cancel",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        json={"reason": "validation suite teardown"},
    )
    assert r.status_code == 200, r.text

    rows = _list_audit_rows_for_voucher(http, scenario, voucher["id"])
    assert len(rows) >= 3, (
        f"expected ≥3 audit rows after create/update/cancel, got "
        f"{len(rows)}: {[x['action'] for x in rows]}"
    )
    assert rows[0]["action"] == "voucher.created"
    assert rows[-1]["action"] == "voucher.cancelled"


@pytest.mark.criterion(
    section="7.3",
    label=(
        "As app user, run `UPDATE audit_logs SET action='x' WHERE id=...` "
        "→ expected: trigger raises exception"
    ),
)
def test_audit_04_direct_update_is_blocked_by_trigger(
    http: httpx.Client,
    scenario: Scenario,
    db_conn,  # type: ignore[no-untyped-def]
) -> None:
    voucher = create_voucher(http, scenario)
    rows = _list_audit_rows_for_voucher(http, scenario, voucher["id"])
    audit_id = UUID(rows[0]["id"])

    import psycopg

    # The trigger raises `audit_logs is append-only` and aborts the
    # transaction. Anything else (no error, or wrong sqlstate) fails.
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            cur.execute(
                "UPDATE audit_logs SET action = 'x' WHERE id = %s",
                (audit_id,),
            )
        db_conn.rollback()
    assert "append-only" in str(excinfo.value).lower() or (
        "audit_logs" in str(excinfo.value).lower()
    ), f"unexpected error message: {excinfo.value!s}"


@pytest.mark.criterion(
    section="7.3",
    label=(
        "As viewer-role user, GET /audit-logs/ → expected 403"
    ),
)
def test_audit_05_viewer_role_get_audit_logs_returns_403(
    http: httpx.Client, scenario: Scenario
) -> None:
    viewer = register_and_login(http)
    add_member(
        http,
        scenario.owner,
        company_id=scenario.company_id,
        email=viewer.email,
        role="viewer",
    )
    r = http.get(
        "/api/v1/audit-logs/",
        headers=auth_headers(viewer, company_id=scenario.company_id),
    )
    assert r.status_code == 403, (
        f"expected 403 for viewer reading audit logs, got "
        f"{r.status_code}: {r.text}"
    )


_SECRET_RE = re.compile(
    r"Hunter2-Validation!|"  # the literal password we use to register
    r"ey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    re.IGNORECASE,
)


@pytest.mark.criterion(
    section="7.3",
    label=(
        "No audit log row contains literal password/token/api_key value"
    ),
)
def test_audit_06_no_secret_literal_in_audit_payload(
    http: httpx.Client, scenario: Scenario
) -> None:
    # Provoke a few audit-emitting paths: create a voucher and update
    # it. Any of them could in principle accidentally serialize a
    # secret if the redactor missed something.
    voucher = create_voucher(http, scenario)
    r = http.patch(
        f"/api/v1/vouchers/{voucher['id']}",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        json={"narration": f"redaction-probe-{uuid4().hex[:6]}"},
    )
    assert r.status_code == 200

    r = http.get(
        "/api/v1/audit-logs/",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        params={"limit": 200},
    )
    assert r.status_code == 200, r.text
    payload = json.dumps(r.json())  # full serialization, all fields

    match = _SECRET_RE.search(payload)
    assert match is None, (
        f"audit payload contains a literal that looks like a secret: "
        f"{match.group(0)[:40]!r}"
    )
    # Belt-and-braces: no key with the literal name 'password' should
    # appear with a value other than the redactor sentinel.
    items = r.json()["items"]
    for row in items:
        for blob_key in ("old_value", "new_value", "changes"):
            blob = row.get(blob_key) or {}
            for k, v in (blob.items() if isinstance(blob, dict) else []):
                if re.search(
                    r"(password|secret|token|api[_-]?key)", k, re.IGNORECASE
                ):
                    assert v == "***REDACTED***", (
                        f"row {row['id']} has unredacted sensitive key "
                        f"{k!r}={v!r}"
                    )
