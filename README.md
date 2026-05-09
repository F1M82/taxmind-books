# TaxMind Books

MSME-focused accounting automation for India. Captures accounting entries from
the channels small businesses already use (photos of bills, SMS, bank
statements, voice) and posts them to **TallyPrime** automatically, with a
CA-grade audit trail and a reconciliation review queue.

> **Status:** Phase 0 in progress. Architecture frozen at v1.2. See
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
> [`docs/AMENDMENTS_v1.2.md`](docs/AMENDMENTS_v1.2.md).

## Repository layout

The frozen tree lives in [`docs/REPO_LAYOUT.md`](docs/REPO_LAYOUT.md). Briefly:

```
taxmind-books/
├── backend/      # FastAPI cloud API + Celery workers
├── connector/    # Tally Desktop Connector (Windows agent)
├── mobile/       # React Native (Expo) app — primary UI
├── web/          # Vite + React admin/CA console (Phase 5)
├── docs/         # Canonical architecture documents (the source of truth)
├── ops/          # Deployment, runbooks, env templates
└── tools/        # Developer utilities (validation, lint scripts)
```

## Document map

| Document | What it defines |
|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | Rules of engagement; non-negotiable |
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Vision, scope, phasing, navigation |
| [`AMENDMENTS_v1.2.md`](docs/AMENDMENTS_v1.2.md) | v1.1 → v1.2 deltas |
| [`REPO_LAYOUT.md`](docs/REPO_LAYOUT.md) | Frozen directory tree, naming, boundaries |
| [`MONEY.md`](docs/MONEY.md) | Decimal-only money handling |
| [`TENANCY.md`](docs/TENANCY.md) | Multi-tenant scoping rules |
| [`AUDIT.md`](docs/AUDIT.md) | Service-layer audit emitter, append-only |
| [`IDEMPOTENCY.md`](docs/IDEMPOTENCY.md) | Idempotency-Key contract |
| [`SCHEMA.sql`](docs/SCHEMA.sql) | Full PostgreSQL DDL |
| [`API.md`](docs/API.md) | HTTP endpoint contracts |
| [`CONNECTOR_PROTOCOL.md`](docs/CONNECTOR_PROTOCOL.md) | Tally Connector wire protocol |
| [`EXTRACTION_CONTRACT.md`](docs/EXTRACTION_CONTRACT.md) | LLM extraction schema |
| [`REPORTS.md`](docs/REPORTS.md) | Report computation rules |
| [`TESTING.md`](docs/TESTING.md) | Test architecture, fixtures, CI gates |
| [`PHASE_0_TASKS.md`](docs/PHASE_0_TASKS.md) | The 46 atomic Phase 0 tasks |
| [`VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) | Phase validation template |

## Quick start (local development)

> Prerequisites: Docker Desktop, Python 3.11, Git. Backend tooling lands in P0.02.

```bash
git clone https://github.com/F1M82/taxmind-books.git
cd taxmind-books
cp .env.example .env

# Start postgres + redis (the P0.01 acceptance check)
docker compose up -d

# Verify both are healthy
docker compose ps
```

Once P0.02 lands, the backend service joins via the `app` profile:

```bash
docker compose --profile app up -d
```

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
