"""§7.2 Tenant isolation (per TENANCY.md).

Each test maps to one acceptance criterion from `docs/VALIDATION_REPORT.md`
§7.2. The shape is always: User A in Company A; an attempt to read or
write data in / for Company B; assert the precise refusal.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from conftest import (
    Scenario,
    auth_headers,
    create_company,
    create_ledger,
    create_voucher,
    register_and_login,
    voucher_payload,
)


@pytest.mark.criterion(
    section="7.2",
    label=(
        "User A in company A only; GET /vouchers/{id} for a voucher in "
        "company B → expected 404"
    ),
)
def test_tenancy_01_cross_company_voucher_get_returns_404(
    http: httpx.Client, scenario: Scenario
) -> None:
    # Scenario gives us User A + Company A + a voucher belonging to A.
    voucher_b = create_voucher(http, scenario)
    # Now build User B + Company B.
    user_b = register_and_login(http)
    company_b = create_company(http, user_b)
    # User B fetches the (A-owned) voucher with X-Company-ID=B.
    # Two acceptable outcomes per TENANCY.md: 404 because the voucher
    # isn't in B's company, or 404 because B has no membership in A.
    r = http.get(
        f"/api/v1/vouchers/{voucher_b['id']}",
        headers=auth_headers(user_b, company_id=company_b),
    )
    assert r.status_code == 404, (
        f"expected 404 cross-tenant, got {r.status_code}: {r.text}"
    )


@pytest.mark.criterion(
    section="7.2",
    label=(
        "User A; POST /vouchers/ with X-Company-ID = B → expected 404"
    ),
)
def test_tenancy_02_cross_company_voucher_post_returns_404(
    http: httpx.Client, scenario: Scenario
) -> None:
    # User B owns Company B; User A (from the scenario) is not a
    # member of B. Posting with X-Company-ID=B must 404.
    user_b = register_and_login(http)
    company_b = create_company(http, user_b)
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner, company_id=company_b, idem=uuid4().hex
        ),
        json=voucher_payload(
            scenario.bank_ledger_id, scenario.party_ledger_id
        ),
    )
    assert r.status_code == 404, (
        f"expected 404 for non-member X-Company-ID, got {r.status_code}: "
        f"{r.text}"
    )


@pytest.mark.criterion(
    section="7.2",
    label=(
        "User A; POST with body `company_id: \"<B>\"` and X-Company-ID = A "
        "→ expected 201 with company_id=A OR 422"
    ),
)
def test_tenancy_03_body_company_id_ignored_or_422(
    http: httpx.Client, scenario: Scenario
) -> None:
    user_b = register_and_login(http)
    company_b = create_company(http, user_b)
    payload = voucher_payload(
        scenario.bank_ledger_id, scenario.party_ledger_id
    )
    payload["company_id"] = str(company_b)  # foreign company in body
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner,
            company_id=scenario.company_id,
            idem=uuid4().hex,
        ),
        json=payload,
    )
    if r.status_code == 201:
        # Accepted — pydantic must have dropped the extra `company_id`
        # and the voucher must be filed under company A (the header).
        assert r.json()["company_id"] == str(scenario.company_id), (
            "body's company_id should have been ignored; voucher landed "
            f"in {r.json()['company_id']} instead of {scenario.company_id}"
        )
    elif r.status_code == 422:
        # Also acceptable per TENANCY.md — strict schemas may reject.
        pass
    else:
        pytest.fail(
            f"expected 201 (with company_id=A) or 422, got "
            f"{r.status_code}: {r.text}"
        )


@pytest.mark.criterion(
    section="7.2",
    label=(
        "User A; GET /vouchers/ without X-Company-ID → expected 422"
    ),
)
def test_tenancy_04_missing_x_company_id_returns_422(
    http: httpx.Client, scenario: Scenario
) -> None:
    # No X-Company-ID header at all.
    r = http.get(
        "/api/v1/vouchers/",
        headers={"Authorization": f"Bearer {scenario.owner.access_token}"},
    )
    assert r.status_code == 422, (
        f"expected 422 for missing X-Company-ID, got {r.status_code}: "
        f"{r.text}"
    )


@pytest.mark.criterion(
    section="7.2",
    label=(
        "User A; GET /vouchers/?company_id=<B> → query param ignored, "
        "only A's data returned"
    ),
)
def test_tenancy_05_query_param_company_id_ignored(
    http: httpx.Client, scenario: Scenario
) -> None:
    # Plant data in BOTH companies so we can prove the response holds
    # only A's rows.
    voucher_a = create_voucher(http, scenario, narration="company-A row")
    user_b = register_and_login(http)
    company_b = create_company(http, user_b)
    bank_b = create_ledger(
        http,
        user_b,
        company_id=company_b,
        name=f"Bank-B-{uuid4().hex[:4]}",
        group_name="Bank Accounts",
        balance_type="Dr",
    )
    party_b = create_ledger(
        http,
        user_b,
        company_id=company_b,
        name=f"Party-B-{uuid4().hex[:4]}",
        group_name="Sundry Debtors",
        balance_type="Dr",
    )
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            user_b, company_id=company_b, idem=uuid4().hex
        ),
        json=voucher_payload(
            bank_b, party_b, narration="company-B row"
        ),
    )
    assert r.status_code == 201, r.text
    voucher_b_id = r.json()["id"]

    # User A lists with a deceptive query param pointing at Company B.
    # `VoucherListItem` doesn't echo `company_id`, so we verify
    # isolation by ID membership: A's voucher must appear, B's must
    # not — proving the query param was ignored and the active
    # company (from X-Company-ID) drove the scope.
    r = http.get(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner, company_id=scenario.company_id
        ),
        params={"company_id": str(company_b)},
    )
    assert r.status_code == 200, r.text
    items = r.json().get("items", [])
    returned_ids = {item["id"] for item in items}
    assert voucher_a["id"] in returned_ids, (
        "company A's voucher missing — list must include A's rows"
    )
    assert voucher_b_id not in returned_ids, (
        f"company B's voucher {voucher_b_id} leaked into A's list — "
        f"the query-param company_id was NOT ignored"
    )
