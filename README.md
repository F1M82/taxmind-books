# TaxMind Books

MSME-focused accounting automation for India. Captures accounting entries from
the channels small businesses already use (photos of bills, SMS, bank
statements, voice) and posts them to **TallyPrime** automatically, with a
CA-grade audit trail and a reconciliation review queue.

> **Status:** Phase 0 substantially complete (P0.01 → P0.34 landed, P0.35 +
> P0.36-P0.46 in-flight). Architecture frozen at v1.2 (+ Patches 1 & 2).
> Backend: 436 tests passing. Connector: 40. Mobile: structural scaffolding.

## Quick start

A new developer should be running the backend in under ten minutes:

```bash
git clone https://github.com/F1M82/taxmind-books.git
cd taxmind-books
cp .env.example .env             # populate JWT_SECRET etc. — placeholders are fine for local

# 1. Start postgres + redis
docker compose up -d postgres redis

# 2. Backend
cd backend
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # (Linux/mac: source .venv/bin/activate)
pip install -e ".[dev]"
DATABASE_URL=postgresql+psycopg://taxmind:taxmind@localhost:5432/taxmind_books \
  alembic upgrade head
DATABASE_URL=postgresql+psycopg://taxmind:taxmind@localhost:5432/taxmind_books \
  REDIS_URL=redis://localhost:6379/0 \
  JWT_SECRET=x SECRET_KEY=x CONNECTOR_JWT_SECRET=x \
  uvicorn app.main:app --reload
# Open http://localhost:8000/docs for the OpenAPI explorer
```

Full instructions, troubleshooting, and the connector / mobile setup live in
[`docs/SETUP.md`](docs/SETUP.md).

## Repository layout

The frozen tree is in [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md). Briefly:

```
taxmind-books/
├── backend/         # FastAPI cloud API + Celery workers + alembic
│   ├── app/         #   ↳ models, schemas, services, api/v1, core, workers
│   ├── alembic/     #   ↳ migrations (head: 0006)
│   └── tests/       #   ↳ unit / integration / tenant_isolation (436 tests)
├── connector/       # Tally Desktop Connector (Windows .exe via PyInstaller)
│   ├── connector/   #   ↳ tally_client, ws_client, message_handlers
│   ├── installer/   #   ↳ PyInstaller build script
│   └── tests/       #   ↳ unit + integration (40 tests)
├── mobile/          # React Native (Expo) app — primary UI
│   ├── src/         #   ↳ api, context, navigation, screens, utils
│   └── tests/       #   ↳ jest + @testing-library/react-native
├── docs/            # Architecture documents (the source of truth)
├── tools/           # Static checks (money, audit, imports)
└── salvage/         # Read-only reference material from the prior repo
```

## Document map

| Document | What it defines |
|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | Rules of engagement; non-negotiable |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Vision, scope, phasing, navigation |
| [`docs/AMENDMENTS_v1.2.md`](docs/AMENDMENTS_v1.2.md) | v1.1 → v1.2 deltas + Patches 1 & 2 |
| [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) | Frozen directory tree, module boundaries |
| [`docs/MONEY.md`](docs/MONEY.md) | Decimal-only money handling |
| [`docs/TENANCY.md`](docs/TENANCY.md) | Multi-tenant scoping rules |
| [`docs/AUDIT.md`](docs/AUDIT.md) | Service-layer audit emitter, append-only |
| [`docs/IDEMPOTENCY.md`](docs/IDEMPOTENCY.md) | Idempotency-Key contract |
| [`docs/SCHEMA.sql`](docs/SCHEMA.sql) | Full PostgreSQL DDL |
| [`docs/API.md`](docs/API.md) | HTTP endpoint contracts |
| [`docs/CONNECTOR_PROTOCOL.md`](docs/CONNECTOR_PROTOCOL.md) | Tally Connector wire protocol |
| [`docs/EXTRACTION_CONTRACT.md`](docs/EXTRACTION_CONTRACT.md) | LLM extraction schema |
| [`docs/REPORTS.md`](docs/REPORTS.md) | Report computation rules |
| [`docs/TESTING.md`](docs/TESTING.md) | Test architecture, CI gates |
| [`docs/PHASE_0_TASKS.md`](docs/PHASE_0_TASKS.md) | The 46 atomic Phase 0 tasks |
| [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) | Phase validation template |
| [`docs/SETUP.md`](docs/SETUP.md) | Detailed local-dev setup |

## CI gates

`.github/workflows/ci.yml` enforces five gates, in order:

1. **Lint** — ruff repo-wide, `check_money_types.py`,
   `check_audit_emit.py`, `check_imports.py`.
2. **Type-check** — mypy on `backend/app/`.
3. **Backend** — pytest against a Postgres 16 service container.
   Unit + integration + tenant_isolation + alembic round-trip. Coverage
   floor enforced at 70% via `--cov-fail-under`.
4. **Connector** — pytest on `connector/`.
5. **Mobile** — `tsc --noEmit` + `jest`.

Connector binary builds run in `.github/workflows/connector-build.yml` on
push to `main` and on Releases.

## Phasing

| Phase | Deliverable | Effort (solo, v1.2) |
|---|---|---|
| 0 | Auth, multi-tenancy, audit, manual vouchers, Tally connector, basic reports, dashboard, push infra, onboarding, DPDP deletion | 5–6 weeks |
| 1 | Invoice scan via Optional vouchers, analytics, data export, push triggers, cost tracking, rate limiting | ~5.5 weeks |
| 2 | Bank statement ingestion, SMS, Email IMAP | 6 weeks |
| 3 | Reconciliation, GSTR-2B matching | 4 weeks |
| 4 | (deferred) WhatsApp gateway | 6 weeks |
| 5 | CA console, Razorpay billing, advanced reports | 4 weeks |

## License

Proprietary. See [`LICENSE`](LICENSE). For inquiries:
`gaurav.chandaliya21@gmail.com`.
