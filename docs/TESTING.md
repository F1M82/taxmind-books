# Testing Architecture

**Status:** Frozen.

The constitution Section 7 mandates that every implementation includes happy-path, edge-case, failure-mode, idempotency, and tenant-isolation tests. This document specifies the framework, structure, fixtures, and CI gates that make those tests cheap to write and impossible to skip.

## Why this matters

The prior contractors delivered a 1,871-line test suite that could not collect a single test (Qwen-Tallyonmobile) and zero tests at all (taxmind-recon). The first asset we lose without a real test discipline is confidence that any past success still works. The second is the ability to refactor anything safely. Both are essential for a solo founder who cannot manually re-test the world after every change.

## The test pyramid for TaxMind Books

```
                    ┌────────────────────┐
                    │   E2E (very few)   │   Phase 5+
                    └────────────────────┘
                  ┌────────────────────────┐
                  │ Integration tests      │   Real DB, real Redis
                  │ (most coverage here)   │   Mocked external APIs
                  └────────────────────────┘
              ┌────────────────────────────────┐
              │ Unit tests (pure functions)    │   No DB, no I/O
              └────────────────────────────────┘
            ┌────────────────────────────────────┐
            │ Tenant isolation tests (mandatory) │   Per endpoint
            └────────────────────────────────────┘
```

Different layers exist for different reasons:
- **Unit:** fast, deterministic checks of pure logic. Money math, GST rate computation, GSTIN regex.
- **Integration:** the bulk. Real Postgres in a container, real Redis, FastAPI test client. Mocks for Anthropic, S3, Tally connector.
- **Tenant isolation:** a dedicated tier because the constitution treats tenant isolation as non-negotiable. Every endpoint has at least one isolation test.
- **E2E:** real connector, real Tally instance, real mobile app. Manual or scripted; not part of CI. Phase 5+.

## Framework

### Backend

- **Test runner:** `pytest` 8.x
- **Async support:** `pytest-asyncio` (mode = strict; asyncio markers required)
- **HTTP client:** `httpx.AsyncClient` against `app` directly (no live server needed)
- **DB:** `pytest-postgresql` for ephemeral Postgres per test session, OR a dedicated `taxmind_books_test` database per CI runner. Migration applied at session setup.
- **Factories:** `factory_boy` for model factories (one factory per model, in `tests/factories/`)
- **Mocks:** `pytest-mock` (no `unittest.mock` direct usage; consistent style)
- **Coverage:** `coverage.py` with branch coverage; threshold 80% for `app/services/`, 90% for `app/core/`, 70% overall

### Mobile

- **Test runner:** `jest` with `jest-expo` preset
- **Component tests:** `@testing-library/react-native`
- **Mock:** API client mocked with `msw/native` for component tests
- **Coverage:** 60% threshold (UI tests are expensive; we don't chase coverage there)

### Web

- Same as mobile, less ambitious. Web is the second-class citizen until Phase 5.

### Connector

- **Test runner:** `pytest`
- **Tally mocking:** `pytest-httpx` mocks the Tally HTTP server. The connector makes calls to a fake `localhost:9000` that returns canned XML responses.
- **WebSocket testing:** `pytest-asyncio` with `httpx_ws` for WS client tests. The backend WS endpoint is also tested with the FastAPI test client (which has WS support).

## Test directory layout

Per `REPO_LAYOUT.md`:

```
backend/tests/
├── conftest.py                  # session-scoped fixtures: db, client, settings
├── factories/                   # one file per model
│   ├── __init__.py
│   ├── user_factory.py
│   ├── company_factory.py
│   ├── ledger_factory.py
│   ├── voucher_factory.py
│   └── ...
├── unit/
│   ├── core/
│   │   ├── test_money.py
│   │   ├── test_audit.py
│   │   └── test_idempotency.py
│   └── services/
│       ├── test_party_matcher.py
│       └── test_extraction_validator.py
├── integration/
│   ├── api/
│   │   ├── test_auth.py
│   │   ├── test_companies.py
│   │   ├── test_vouchers.py
│   │   └── ...
│   ├── workers/
│   │   └── test_extraction_worker.py
│   └── connector/
│       └── test_ws_handshake.py
├── tenant_isolation/            # mandatory per endpoint
│   ├── test_companies_isolation.py
│   ├── test_vouchers_isolation.py
│   └── ...
├── golden/                      # known-good snapshot tests
│   ├── test_invoice_extraction_corpus.py
│   ├── test_recon_engine_corpus.py
│   └── test_sms_parser_corpus.py
└── fixtures/                    # test data files
    ├── invoices/
    │   ├── typed_gst_01.pdf
    │   ├── typed_gst_01_expected.json
    │   ├── cash_memo_01.jpg
    │   └── ...
    ├── bank_statements/
    ├── sms_corpus/
    └── party_statements/
```

## Fixtures

### Database fixtures

```python
# backend/tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base


@pytest.fixture(scope="session")
def db_engine():
    """Session-scoped engine; created once, dropped at teardown."""
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db(db_engine):
    """Per-test session with rollback isolation."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()                 # everything rolls back
    connection.close()
```

The `db` fixture wraps each test in a transaction that rolls back at teardown. Tests cannot leak state to each other. This is the single most important property of the test suite.

### App fixture

```python
@pytest.fixture
def app(db):
    """FastAPI app with the test db dependency override."""
    from app.main import create_app
    from app.core.database import get_db

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture
async def client(app):
    """Async HTTP client for the test app."""
    from httpx import AsyncClient
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c
```

### Auth fixtures

```python
@pytest.fixture
def user_factory(db):
    def _make(**kwargs):
        from tests.factories.user_factory import UserFactory
        u = UserFactory(**kwargs)
        db.add(u)
        db.flush()
        return u
    return _make


@pytest.fixture
def authed_client(client, user_factory, company_factory):
    """Returns (client_with_auth_headers, user, company)."""
    user = user_factory()
    company = company_factory(created_by=user)
    # Add user as owner
    membership = UserCompanyFactory(user_id=user.id, company_id=company.id, role="owner")
    db.add(membership)
    db.flush()
    
    token = create_access_token({"sub": str(user.id)})
    client.headers["Authorization"] = f"Bearer {token}"
    client.headers["X-Company-ID"] = str(company.id)
    return client, user, company
```

This is the most-used fixture in integration tests. It sets up an authenticated request with a fresh user and company in three lines.

### Two-tenant fixture

```python
@pytest.fixture
def two_tenants(db, user_factory, company_factory):
    """For tenant isolation tests: two users, two companies, no overlap."""
    user_a = user_factory(email="a@test.com")
    company_a = company_factory(created_by=user_a)
    db.add(UserCompanyFactory(user_id=user_a.id, company_id=company_a.id, role="owner"))
    
    user_b = user_factory(email="b@test.com")
    company_b = company_factory(created_by=user_b)
    db.add(UserCompanyFactory(user_id=user_b.id, company_id=company_b.id, role="owner"))
    
    db.flush()
    return SimpleNamespace(user_a=user_a, company_a=company_a, user_b=user_b, company_b=company_b)
```

Every tenant isolation test uses this fixture. The pattern: user A acts; we verify user B's data is untouched.

### External service mocks

| Service | Mock |
|---|---|
| Anthropic API | `pytest-httpx` intercepts; returns canned JSON |
| OpenAI API | Same |
| S3 | `moto[s3]` library — real S3 API, in-memory backend |
| Tally connector | A fake `WebSocket` that records sent messages and replays scripted responses |
| Razorpay | Mock with `pytest-mock`; not exercised in v1 phases |
| Email IMAP | Mock; integration tests use `aioimaplib` mock server |

External services are NEVER hit from tests. CI without internet still passes.

## Test categories — what each one looks like

### Unit test — pure function

```python
# backend/tests/unit/core/test_money.py
from decimal import Decimal
import pytest
from app.core.money import money_add, format_inr_paisa

class TestMoneyAdd:
    def test_basic_addition(self):
        assert money_add(Decimal("1.10"), Decimal("2.20")) == Decimal("3.30")

    def test_quantizes_to_two_places(self):
        result = money_add(Decimal("1.10"), Decimal("2.205"))
        assert result == Decimal("3.31")           # banker's rounding
        assert str(result) == "3.31"

    def test_rejects_float(self):
        with pytest.raises(TypeError):
            money_add(Decimal("1.00"), 2.5)        # type: ignore

    @pytest.mark.parametrize("a,b,expected", [
        ("0", "0", "0.00"),
        ("100000000.00", "0.01", "100000000.01"),
        ("0.005", "0.005", "0.01"),                # 0.005 + 0.005 = 0.01 (banker's)
        ("0.005", "0.015", "0.02"),
    ])
    def test_edge_cases(self, a, b, expected):
        assert str(money_add(Decimal(a), Decimal(b))) == expected
```

### Integration test — endpoint

```python
# backend/tests/integration/api/test_vouchers.py
import pytest

class TestCreateVoucher:
    async def test_creates_balanced_voucher(self, authed_client, ledger_factory):
        client, user, company = authed_client
        bank = ledger_factory(company=company, name="Bank", group_name="Bank Accounts")
        party = ledger_factory(company=company, name="Sharma Traders", group_name="Sundry Debtors")
        
        response = await client.post(
            "/api/v1/vouchers/",
            headers={"Idempotency-Key": "test-key-001"},
            json={
                "voucher_type": "Receipt",
                "date": "2026-05-08",
                "narration": "Payment from Sharma Traders",
                "total_amount": "50000.00",
                "entries": [
                    {"ledger_id": str(bank.id), "amount": "50000.00", "entry_type": "Dr"},
                    {"ledger_id": str(party.id), "amount": "50000.00", "entry_type": "Cr"},
                ],
            },
        )
        
        assert response.status_code == 201
        body = response.json()
        assert body["total_amount"] == "50000.00"           # money is string
        assert body["status"] == "posted"
        assert len(body["entries"]) == 2

    async def test_rejects_unbalanced_voucher(self, authed_client, ledger_factory):
        # Dr total != Cr total
        ...
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "voucher_entries_unbalanced"

    async def test_rejects_float_total(self, authed_client, ledger_factory):
        # body has total_amount as a JSON number (float)
        ...
        assert response.status_code == 422

    async def test_writes_audit_log(self, authed_client, db, ledger_factory):
        # ... create voucher ...
        from app.models.audit_log import AuditLog
        log = db.query(AuditLog).filter(
            AuditLog.entity_type == "voucher",
            AuditLog.action == "voucher.created",
        ).first()
        assert log is not None
        assert log.user_id == user.id
        assert log.new_value["total_amount"] == "50000.00"

    async def test_idempotent_replay(self, authed_client, ledger_factory):
        # Two POSTs with same Idempotency-Key, same body
        # Second returns 201 with same id and Idempotent-Replay: true header
        ...

    async def test_idempotent_body_mismatch(self, authed_client, ledger_factory):
        # Two POSTs with same key, different body → 409 idempotency_replay
        ...

    async def test_requires_idempotency_key(self, authed_client, ledger_factory):
        # POST without header → 400 idempotency_key_required
        ...
```

This test file is the template. Every endpoint has its own test file with this shape.

### Tenant isolation test

```python
# backend/tests/tenant_isolation/test_vouchers_isolation.py
class TestVoucherTenantIsolation:
    async def test_cannot_read_other_company_voucher(self, two_tenants, voucher_factory, client):
        v_in_b = voucher_factory(company=two_tenants.company_b)
        token_a = create_access_token({"sub": str(two_tenants.user_a.id)})
        
        response = await client.get(
            f"/api/v1/vouchers/{v_in_b.id}",
            headers={
                "Authorization": f"Bearer {token_a}",
                "X-Company-ID": str(two_tenants.company_a.id),  # A, not B
            },
        )
        assert response.status_code == 404                    # NOT 403; see TENANCY.md

    async def test_cannot_set_x_company_id_to_other_company(self, two_tenants, client):
        token_a = create_access_token({"sub": str(two_tenants.user_a.id)})
        
        response = await client.get(
            "/api/v1/vouchers/",
            headers={
                "Authorization": f"Bearer {token_a}",
                "X-Company-ID": str(two_tenants.company_b.id),  # user_a doesn't belong
            },
        )
        assert response.status_code == 404                    # company_not_found

    async def test_body_company_id_ignored(self, two_tenants, client, ledger_factory):
        # POST voucher with X-Company-ID = A but body claims company_id = B
        # Result: voucher lands in A, not B (or 422)
        ...

    async def test_list_scoped_to_active_company(self, two_tenants, voucher_factory, client):
        v_in_a = voucher_factory(company=two_tenants.company_a)
        v_in_b = voucher_factory(company=two_tenants.company_b)
        token_a = create_access_token({"sub": str(two_tenants.user_a.id)})
        
        response = await client.get(
            "/api/v1/vouchers/",
            headers={
                "Authorization": f"Bearer {token_a}",
                "X-Company-ID": str(two_tenants.company_a.id),
            },
        )
        ids = [v["id"] for v in response.json()["items"]]
        assert str(v_in_a.id) in ids
        assert str(v_in_b.id) not in ids
```

### Golden test — extraction corpus

```python
# backend/tests/golden/test_invoice_extraction_corpus.py
import json
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "invoices"


@pytest.mark.parametrize("invoice_path", list(FIXTURES.glob("typed_gst_*.pdf")))
async def test_typed_gst_extraction(invoice_path, extraction_worker):
    expected = json.loads((invoice_path.with_suffix(".expected.json")).read_text())
    
    # The worker uses the real LLM; this test runs nightly, not on every PR
    result = await extraction_worker.extract(invoice_path.read_bytes(), "application/pdf")
    
    assert result.vendor_gstin == expected["vendor_gstin"]
    assert abs(Decimal(result.total_amount) - Decimal(expected["total_amount"])) <= Decimal("1.00")
    assert result.confidence_score >= 0.85
```

The golden tests live behind a `pytest.mark.golden` marker. PR-time CI runs `pytest -m "not golden"`. Nightly CI runs `pytest -m golden`. Result of nightly run posted to a Slack/email channel.

## Test markers

```python
# backend/tests/conftest.py
def pytest_configure(config):
    config.addinivalue_line("markers", "golden: nightly-only tests that hit real LLM/external APIs")
    config.addinivalue_line("markers", "slow: tests that take >5 seconds")
    config.addinivalue_line("markers", "tenant_isolation: tests that verify cross-tenant boundaries")
    config.addinivalue_line("markers", "audit: tests that verify audit log behavior")
```

The `tenant_isolation` and `audit` markers exist so we can run "the security-critical subset" as a fast pre-merge gate even if we skip slower tests in some flows.

## CI gates

Every PR triggers the gate. Failure blocks merge.

### Layer 1: lint + type check (~30 seconds)

```yaml
- ruff check backend/
- ruff format --check backend/
- mypy backend/app/ --strict
- python tools/lint/check_money_types.py
- python tools/lint/check_audit_emit.py
- python tools/lint/check_imports.py
```

### Layer 2: unit + integration tests (~2-3 minutes)

```yaml
- pytest backend/tests/unit/ backend/tests/integration/ -m "not golden and not slow" --cov --cov-fail-under=80
```

Coverage threshold checked here. Failure → CI red.

### Layer 3: tenant isolation tests (~30 seconds)

```yaml
- pytest backend/tests/tenant_isolation/ -m "tenant_isolation"
```

Separate gate so failures here are unmistakably about security.

### Layer 4: migration round-trip (~30 seconds)

```yaml
- alembic upgrade head
- alembic downgrade base
- alembic upgrade head                # both directions work
```

### Layer 5: API contract test (~30 seconds)

```yaml
- pytest backend/tests/integration/test_openapi_contract.py
```

Verifies the auto-generated OpenAPI matches `docs/API.md`.

### Layer 6 (nightly only): golden + slow

```yaml
- pytest -m golden
- pytest -m slow
```

Total CI time PR-gate: ~4-5 minutes. Acceptable for a solo founder workflow.

## What "passing" means per task

Per Section 8 of the constitution, every task's acceptance gate includes:

1. ✅ Unit tests for the new logic (happy path, 3+ failure modes)
2. ✅ Integration tests for new endpoints (request/response, side effects, audit, idempotency)
3. ✅ Tenant isolation test if the endpoint is tenant-scoped
4. ✅ Coverage above threshold for changed files
5. ✅ Migration runs forward AND backward
6. ✅ OpenAPI contract test passes (no API drift)
7. ✅ All existing tests still pass (no regressions)

A task that ships without these is incomplete. Coder Claude does not declare a task done until all seven check.

## Test data hygiene

- **No real PII in fixtures.** Vendor names in invoice fixtures are fictional (Sharma Traders, Acme Industries). Phone numbers are 1234567890. Emails are @test.com.
- **No real GSTINs.** Test GSTINs follow the format but use 0s and AAAAA letters (e.g., `27AAAAA0000A1Z5`).
- **No actual customer data.** Even anonymized. If a real invoice is being added to fixtures, every identifying field is replaced before commit.
- **Fixtures committed to git.** Yes, they grow the repo, but they are essential for reproducibility. We cap at 50 invoices total in `fixtures/invoices/` (10 typed + 5 cash + 5 handwritten + 30 reserved for diversity).

## Test development discipline

When adding a feature, write tests in this order:

1. **Failing integration test** — write the test that would pass if the feature existed. Confirm it fails.
2. **Implement the simplest thing** that makes the test pass.
3. **Add edge cases** — write 3+ failure-mode tests. Each starts failing, then you fix.
4. **Add tenant isolation test** if the feature touches tenant data.
5. **Add audit-log assertion** if the feature changes financial state.
6. **Verify coverage** — ensure new lines are covered.

This order prevents writing implementation that "looks done" but isn't tested. The discipline is what separates code Coder Claude declares done from code that actually works.

## Forbidden patterns

- **Tests that hit real external APIs in CI.** No real Anthropic, no real OpenAI, no real Tally. (Nightly golden runs are an exception, isolated.)
- **Tests that depend on test order.** Each test runs independently; rollback fixture ensures isolation.
- **Tests with `time.sleep()`.** If timing matters, use freezegun.
- **Tests with hardcoded UUIDs, dates, or amounts** that aren't fixture-managed. Use factories.
- **Skipping tests with `pytest.mark.skip` without an open ticket reference** in the comment.
- **Commented-out tests.** Delete them; if they should run, fix them.
- **Tests in production code paths.** No `if testing:` branches in `app/`.

## Test cases the human runs during validation

The validation report (see `VALIDATION_REPORT.md`) includes a Test Suite section:

1. `pytest backend/tests/unit/` — exits 0, all tests pass
2. `pytest backend/tests/integration/` — exits 0, all tests pass
3. `pytest backend/tests/tenant_isolation/` — exits 0, all tests pass
4. `coverage report` — overall ≥80%, app/services ≥80%, app/core ≥90%
5. `alembic upgrade head && alembic downgrade base && alembic upgrade head` — exits 0
6. Test count ≥ specified minimum for the phase (Phase 0: 80+, Phase 1: 150+)

If any check fails, the phase does not pass.
