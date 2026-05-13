"""Live-API validation suite — shared fixtures and reporting hook.

This suite automates Sections 7.1–7.4 of `docs/VALIDATION_REPORT.md`
(Money, Tenancy, Audit, Idempotency). It is NOT part of the regular
CI test pipeline — it requires a running backend at
`http://localhost:8000` (override via ``VALIDATION_BASE_URL``) and,
for a small number of DB-direct assertions, direct Postgres access
(override via ``VALIDATION_DATABASE_URL``; defaults to the same DB
the backend is using).

Running::

    cd backend && uvicorn app.main:app --port 8000   # in one shell
    pytest tests/validation/ -v                       # in another

The session-end hook prints a markdown checklist matching the
report's §7 layout so the human reviewer can paste it straight into
the filled validation report.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_DSN = (
    "postgresql://taxmind:taxmind@localhost:5432/taxmind_books_test"
)


# ---------------------------------------------------------------------
# Reachability — skip the whole suite cleanly if the backend isn't up
# ---------------------------------------------------------------------


def _base_url() -> str:
    return os.environ.get("VALIDATION_BASE_URL", DEFAULT_BASE_URL)


def _db_dsn() -> str:
    raw = os.environ.get(
        "VALIDATION_DATABASE_URL",
        os.environ.get("DATABASE_URL", DEFAULT_DSN),
    )
    # SQLAlchemy-style DSNs ("postgresql+psycopg://…") aren't valid
    # for the bare psycopg driver; strip the dialect prefix.
    if raw.startswith("postgresql+psycopg://"):
        return "postgresql://" + raw[len("postgresql+psycopg://"):]
    return raw


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """If the backend isn't reachable, skip every test with one message."""
    url = _base_url()
    try:
        r = httpx.get(f"{url}/health", timeout=2.0)
        if r.status_code >= 500:
            raise RuntimeError(f"backend /health returned {r.status_code}")
    except Exception as exc:
        skip_marker = pytest.mark.skip(
            reason=(
                f"backend not reachable at {url} ({exc!s}); start it "
                "with `uvicorn app.main:app --port 8000` and rerun"
            )
        )
        for item in items:
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------
# Result tracking for the end-of-session checklist
# ---------------------------------------------------------------------


@dataclass
class CriterionResult:
    section: str  # e.g. "7.1"
    label: str  # human-readable criterion text
    outcome: str = "skipped"  # passed / failed / skipped / errored


@dataclass
class _Tracker:
    results: dict[str, CriterionResult] = field(default_factory=dict)


_TRACKER = _Tracker()


def _record(nodeid: str, section: str, label: str, outcome: str) -> None:
    _TRACKER.results[nodeid] = CriterionResult(
        section=section, label=label, outcome=outcome
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
):  # type: ignore[no-untyped-def]
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    marker = item.get_closest_marker("criterion")
    if marker is None:
        return
    section: str = marker.kwargs.get("section", "?")
    label: str = marker.kwargs.get("label", item.name)
    if report.passed:
        result = "passed"
    elif report.skipped:
        result = "skipped"
    elif report.failed:
        result = "failed"
    else:
        result = "errored"
    _record(item.nodeid, section, label, result)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "criterion(section, label): pin a test to a VALIDATION_REPORT.md "
        "§7 acceptance criterion so the end-of-session checklist can map "
        "it back to the report.",
    )


def pytest_terminal_summary(
    terminalreporter, exitstatus, config  # type: ignore[no-untyped-def]
) -> None:
    if not _TRACKER.results:
        return
    by_section: dict[str, list[CriterionResult]] = {}
    for cr in _TRACKER.results.values():
        by_section.setdefault(cr.section, []).append(cr)
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("VALIDATION_REPORT.md §7 checklist (paste into the report)")
    lines.append("=" * 72)
    for section in sorted(by_section):
        title = {
            "7.1": "7.1 Money handling (per MONEY.md)",
            "7.2": "7.2 Tenant isolation (per TENANCY.md)",
            "7.3": "7.3 Audit log (per AUDIT.md)",
            "7.4": "7.4 Idempotency (per IDEMPOTENCY.md)",
        }.get(section, section)
        lines.append(f"\n### {title}")
        for cr in by_section[section]:
            mark = {
                "passed": "[x]",
                "failed": "[ ] FAIL —",
                "skipped": "[ ] SKIP —",
                "errored": "[ ] ERROR —",
            }[cr.outcome]
            lines.append(f"- {mark} {cr.label}")
    lines.append("")
    terminalreporter.write_line("\n".join(lines))


# ---------------------------------------------------------------------
# HTTP client + API helpers
# ---------------------------------------------------------------------


@pytest.fixture
def base_url() -> str:
    return _base_url()


@pytest.fixture
def http(base_url: str) -> Generator[httpx.Client, None, None]:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        yield client


@pytest.fixture
def db_conn():  # type: ignore[no-untyped-def]
    """Optional psycopg connection. Tests that need it call this fixture;
    others skip cleanly if psycopg or the DSN isn't available."""
    try:
        import psycopg
    except ImportError as exc:
        pytest.skip(f"psycopg not installed: {exc}")
    try:
        conn = psycopg.connect(_db_dsn())
    except Exception as exc:
        pytest.skip(f"Postgres not reachable at {_db_dsn()}: {exc}")
    try:
        yield conn
    finally:
        conn.close()


@dataclass
class UserHandle:
    id: UUID
    email: str
    password: str
    access_token: str


def _unique_email(prefix: str = "v7user") -> str:
    return f"{prefix}-{uuid4().hex[:10]}@example.com"


def register_and_login(
    http: httpx.Client, *, password: str = "Hunter2-Validation!"
) -> UserHandle:
    """Create a fresh user and return its access token."""
    email = _unique_email()
    r = http.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "Validation User",
        },
    )
    assert r.status_code == 201, f"register failed: {r.status_code} {r.text}"
    user_id = UUID(r.json()["id"])

    r = http.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token: str = r.json()["access_token"]
    return UserHandle(
        id=user_id, email=email, password=password, access_token=token
    )


def auth_headers(
    user: UserHandle,
    *,
    company_id: UUID | None = None,
    idem: str | None = None,
) -> dict[str, str]:
    h: dict[str, str] = {"Authorization": f"Bearer {user.access_token}"}
    if company_id is not None:
        h["X-Company-ID"] = str(company_id)
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


def create_company(http: httpx.Client, user: UserHandle, *, name: str | None = None) -> UUID:
    r = http.post(
        "/api/v1/companies/",
        headers=auth_headers(user),
        json={"name": name or f"Acme-{uuid4().hex[:6]}"},
    )
    assert r.status_code == 201, (
        f"create company failed: {r.status_code} {r.text}"
    )
    return UUID(r.json()["id"])


def add_member(
    http: httpx.Client,
    owner: UserHandle,
    *,
    company_id: UUID,
    email: str,
    role: str,
) -> None:
    r = http.post(
        f"/api/v1/companies/{company_id}/members",
        headers=auth_headers(owner),
        json={"email": email, "role": role},
    )
    assert r.status_code == 201, (
        f"add_member failed: {r.status_code} {r.text}"
    )


def create_ledger(
    http: httpx.Client,
    user: UserHandle,
    *,
    company_id: UUID,
    name: str,
    group_name: str,
    balance_type: str = "Dr",
) -> UUID:
    r = http.post(
        "/api/v1/ledgers/",
        headers=auth_headers(user, company_id=company_id),
        json={
            "name": name,
            "group_name": group_name,
            "balance_type": balance_type,
        },
    )
    assert r.status_code == 201, (
        f"create ledger failed: {r.status_code} {r.text}"
    )
    return UUID(r.json()["id"])


def voucher_payload(
    dr_ledger_id: UUID,
    cr_ledger_id: UUID,
    *,
    amount: str = "1500.99",
    voucher_type: str = "Receipt",
    narration: str = "Validation suite test voucher",
) -> dict[str, Any]:
    return {
        "voucher_type": voucher_type,
        "date": "2026-05-08",
        "narration": narration,
        "total_amount": amount,
        "entries": [
            {
                "ledger_id": str(dr_ledger_id),
                "amount": amount,
                "entry_type": "Dr",
            },
            {
                "ledger_id": str(cr_ledger_id),
                "amount": amount,
                "entry_type": "Cr",
            },
        ],
        "gst_applicable": False,
    }


# ---------------------------------------------------------------------
# Per-test bootstrap fixtures
# ---------------------------------------------------------------------


@dataclass
class Scenario:
    """A logged-in owner with a company and two posting ledgers."""

    owner: UserHandle
    company_id: UUID
    bank_ledger_id: UUID
    party_ledger_id: UUID


@pytest.fixture
def scenario(http: httpx.Client) -> Scenario:
    owner = register_and_login(http)
    company_id = create_company(http, owner)
    bank = create_ledger(
        http,
        owner,
        company_id=company_id,
        name=f"Bank-{uuid4().hex[:4]}",
        group_name="Bank Accounts",
        balance_type="Dr",
    )
    party = create_ledger(
        http,
        owner,
        company_id=company_id,
        name=f"Party-{uuid4().hex[:4]}",
        group_name="Sundry Debtors",
        balance_type="Dr",
    )
    return Scenario(
        owner=owner,
        company_id=company_id,
        bank_ledger_id=bank,
        party_ledger_id=party,
    )


def create_voucher(
    http: httpx.Client,
    scenario: Scenario,
    *,
    amount: str = "1500.99",
    voucher_type: str = "Receipt",
    narration: str = "Validation suite test voucher",
    idem: str | None = None,
) -> dict[str, Any]:
    r = http.post(
        "/api/v1/vouchers/",
        headers=auth_headers(
            scenario.owner,
            company_id=scenario.company_id,
            idem=idem or uuid4().hex,
        ),
        json=voucher_payload(
            scenario.bank_ledger_id,
            scenario.party_ledger_id,
            amount=amount,
            voucher_type=voucher_type,
            narration=narration,
        ),
    )
    assert r.status_code == 201, (
        f"create voucher failed: {r.status_code} {r.text}"
    )
    return r.json()


__all__ = [
    "Scenario",
    "UserHandle",
    "add_member",
    "auth_headers",
    "create_company",
    "create_ledger",
    "create_voucher",
    "register_and_login",
    "voucher_payload",
]


# Mark `time`/`json` as deliberately imported so future tests can use
# them without re-importing.
_ = (time, json)
