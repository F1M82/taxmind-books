# Repository Layout

**Status:** Frozen as of v1.1. Any change to this layout requires the Section 1 stop-and-justify flow.

This document is the canonical directory structure for `taxmind-books`. Coder Claude treats it as authoritative. If a file does not have an obvious home per this document, Coder Claude stops and asks rather than inventing a location.

## Top-level tree

```
taxmind-books/
├── .github/
│   └── workflows/
│       ├── ci.yml                 # pytest, mypy, ruff, alembic check
│       └── connector-build.yml    # PyInstaller build of connector exe
├── backend/                        # FastAPI service (the cloud API)
├── connector/                      # Tally Desktop Connector (Windows agent)
├── mobile/                         # React Native + Expo app
├── web/                            # React + Vite admin/CA console
├── docs/                           # Canonical architecture documents
├── ops/                            # Deployment, infra-as-code, runbooks
├── tools/                          # Developer utilities (validation script, etc.)
├── .gitignore
├── .env.example
├── README.md
├── docker-compose.yml              # Local dev: postgres, redis, backend
└── pyproject.toml                  # Workspace-level Python config (ruff, mypy)
```

## Backend layout (`backend/`)

The backend is the entire cloud API. It is one FastAPI application; it is not a microservice cluster. Workers (Celery) run from the same codebase.

```
backend/
├── pyproject.toml                  # Backend Python deps (poetry or pip-tools)
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/                   # Migration files (committed)
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app factory
│   ├── config.py                   # Settings (pydantic-settings)
│   │
│   ├── core/                       # Cross-cutting infrastructure
│   │   ├── __init__.py
│   │   ├── database.py             # Engine, SessionLocal, get_db
│   │   ├── security.py             # JWT, password hashing
│   │   ├── money.py                # Decimal types and helpers (see MONEY.md)
│   │   ├── audit.py                # Audit middleware (see AUDIT.md)
│   │   ├── tenancy.py              # Tenant-scoping dependency (see TENANCY.md)
│   │   ├── idempotency.py          # Idempotency-key handling (see IDEMPOTENCY.md)
│   │   ├── exceptions.py           # Domain exception hierarchy
│   │   └── logging.py              # Structured logging config
│   │
│   ├── models/                     # SQLAlchemy ORM models (one file per aggregate)
│   │   ├── __init__.py             # Exports all models for alembic autogenerate
│   │   ├── base.py                 # DeclarativeBase, common columns
│   │   ├── user.py
│   │   ├── company.py              # Includes UserCompany association
│   │   ├── ledger.py
│   │   ├── voucher.py              # Voucher + LedgerEntry
│   │   ├── ingestion.py            # Ingestion + DraftVoucher
│   │   ├── reconciliation.py       # ReconciliationSession + ReconciliationMatch
│   │   ├── audit_log.py
│   │   ├── sms_template.py
│   │   └── narration_rule.py
│   │
│   ├── schemas/                    # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── company.py
│   │   ├── ledger.py
│   │   ├── voucher.py
│   │   ├── ingestion.py
│   │   ├── reconciliation.py
│   │   └── common.py               # ErrorResponse, PaginationMeta, etc.
│   │
│   ├── api/                        # HTTP route handlers (thin)
│   │   ├── __init__.py
│   │   ├── deps.py                 # FastAPI Depends() functions
│   │   ├── errors.py               # Exception handlers
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py           # Aggregates all v1 sub-routers
│   │       ├── auth.py
│   │       ├── companies.py
│   │       ├── ledgers.py
│   │       ├── vouchers.py
│   │       ├── ingestions.py
│   │       ├── reconciliations.py
│   │       ├── connector_ws.py     # WebSocket endpoint for connector
│   │       └── health.py
│   │
│   ├── services/                   # Business logic (the actual work)
│   │   ├── __init__.py
│   │   ├── auth_service.py
│   │   ├── voucher_service.py      # Create/post/cancel vouchers, audit-aware
│   │   ├── ledger_service.py
│   │   ├── ingestion_service.py    # Coordinates capture → extract → match → review
│   │   ├── reconciliation/         # The recon engine, properly modularized
│   │   │   ├── __init__.py
│   │   │   ├── types.py            # Transaction, MatchResult dataclasses
│   │   │   ├── party_matcher.py    # GSTIN→PAN→fuzzy name index
│   │   │   ├── matching_engine.py  # Six-tier bipartite matcher
│   │   │   ├── edge_cases.py       # TDS, timing, 40A(3), duplicates
│   │   │   ├── confidence_scorer.py
│   │   │   ├── excel_parser.py     # Party statement Excel ingestion
│   │   │   ├── pdf_extractor.py    # Party statement PDF ingestion
│   │   │   └── certificate_generator.py  # PDF reconciliation certificate
│   │   ├── extraction/             # Invoice/receipt OCR pipeline
│   │   │   ├── __init__.py
│   │   │   ├── invoice_extractor.py    # Claude Vision wrapper
│   │   │   ├── extraction_schema.py    # Pydantic JSON schema for LLM output
│   │   │   └── extraction_validator.py # Validates extracted data
│   │   ├── sms/
│   │   │   ├── __init__.py
│   │   │   ├── template_parser.py
│   │   │   └── llm_fallback.py
│   │   ├── bank_statement/
│   │   │   ├── __init__.py
│   │   │   ├── csv_parser.py
│   │   │   ├── pdf/                # One file per supported bank
│   │   │   │   ├── hdfc.py
│   │   │   │   ├── sbi.py
│   │   │   │   ├── icici.py
│   │   │   │   ├── axis.py
│   │   │   │   ├── kotak.py
│   │   │   │   └── yes_bank.py
│   │   │   └── narration_matcher.py
│   │   └── tally/
│   │       ├── __init__.py
│   │       ├── connector_registry.py  # Active connector connections
│   │       └── voucher_dispatcher.py  # Sends vouchers to connector
│   │
│   ├── workers/                    # Celery tasks
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── extraction_tasks.py     # Async OCR/extraction jobs
│   │   ├── matching_tasks.py       # Async party-matching jobs
│   │   ├── posting_tasks.py        # Async Tally post jobs (with retry)
│   │   └── reconciliation_tasks.py
│   │
│   └── integrations/               # External service clients (each is replaceable)
│       ├── __init__.py
│       ├── claude_client.py        # Anthropic API wrapper
│       ├── openai_client.py        # OpenAI fallback (Whisper, GPT)
│       ├── s3_client.py            # AWS S3 / R2 for uploads
│       └── razorpay_client.py      # Billing (Phase 5+)
│
└── tests/
    ├── __init__.py
    ├── conftest.py                 # Pytest fixtures (db, client, factories)
    ├── factories/                  # Test data factories per model
    │   └── ...
    ├── unit/                       # Pure-function tests, no DB
    │   ├── core/
    │   ├── services/
    │   └── ...
    ├── integration/                # DB + API tests, real Postgres in container
    │   ├── api/
    │   ├── workers/
    │   └── ...
    ├── tenant_isolation/           # Dedicated tests for multi-tenant safety
    │   └── ...
    ├── fixtures/                   # Test data files
    │   ├── invoices/               # 50 real Indian invoice PDFs/images
    │   ├── bank_statements/        # 6+ real bank statement PDFs
    │   ├── sms_corpus/             # 100+ real bank/UPI SMS messages
    │   └── party_statements/       # Real party Excel/PDF samples
    └── golden/                     # Golden-output tests (per architecture doc)
        └── ...
```

## Connector layout (`connector/`)

The connector is an independent Python program. It does not import from `backend/`. It speaks to the backend via WebSocket and to Tally via HTTP/XML.

```
connector/
├── pyproject.toml                  # Connector deps (smaller than backend)
├── README.md                       # Installation + Tally configuration
├── connector/
│   ├── __init__.py
│   ├── main.py                     # Entry point; reconnect loop
│   ├── config.py                   # Reads .env / registry on Windows
│   ├── tally_client.py             # SALVAGED from old repo (cleaned up)
│   ├── ws_client.py                # WebSocket client to backend
│   ├── message_handlers.py         # Dispatches messages by type
│   ├── offline_queue.py            # Local SQLite queue for offline replay
│   └── installer/
│       ├── build_exe.py            # PyInstaller spec
│       └── icon.ico
└── tests/
    ├── unit/
    └── integration/                # Tested against a real Tally instance (manual)
```

## Mobile layout (`mobile/`)

React Native (Expo). Single-package app. Routes via `@react-navigation`.

```
mobile/
├── app.json                        # Expo config
├── package.json
├── tsconfig.json
├── App.tsx                         # Root with providers
├── src/
│   ├── api/                        # API client (axios, typed)
│   │   ├── client.ts
│   │   ├── auth.ts
│   │   ├── vouchers.ts
│   │   └── ...
│   ├── context/
│   │   ├── AuthContext.tsx
│   │   └── CompanyContext.tsx      # Active company switcher
│   ├── hooks/
│   ├── screens/
│   │   ├── auth/
│   │   ├── dashboard/
│   │   ├── invoice_scan/           # The wedge feature
│   │   ├── voucher_review/
│   │   ├── reconciliation/
│   │   └── settings/
│   ├── components/                 # Shared UI components
│   ├── navigation/
│   │   └── RootNavigator.tsx
│   ├── theme/
│   └── utils/
│       └── money.ts                # Money formatting (Decimal → display)
└── tests/
    └── ...                         # Jest + React Native Testing Library
```

## Web layout (`web/`)

React + Vite. Thin admin/CA console. Mobile is primary; web is the second-class citizen.

```
web/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/                        # Reuses contracts from mobile API client
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx
│   │   ├── ReviewQueue.tsx
│   │   └── Reconciliation.tsx
│   ├── components/
│   └── ...
└── tests/
```

## Docs layout (`docs/`)

Canonical architecture documents. Coder Claude reads from here. You amend here when you want to change architecture.

```
docs/
├── ARCHITECTURE.md                 # Umbrella prose; cross-refs to the rest
├── REPO_LAYOUT.md                  # This file
├── SCHEMA.sql                      # Full DDL
├── API.md                          # API contracts (OpenAPI prose)
├── AUDIT.md                        # Audit middleware design
├── TENANCY.md                      # Multi-tenant scoping design
├── IDEMPOTENCY.md                  # Idempotency contract
├── MONEY.md                        # Money type rules
├── CONNECTOR_PROTOCOL.md           # Tally Connector wire protocol
├── EXTRACTION_CONTRACT.md          # Invoice OCR contract
├── TESTING.md                      # Test architecture
├── PHASE_0_TASKS.md                # Phase 0 atomic task list
└── VALIDATION_REPORT.md            # Human validation report template
```

## Ops layout (`ops/`)

Deployment, infra-as-code, runbooks. Not application code.

```
ops/
├── railway/                        # Railway service configs
│   └── README.md
├── runbooks/                       # On-call runbooks (per incident type)
│   ├── tally_connector_offline.md
│   ├── extraction_failure_spike.md
│   └── database_restore.md
├── migrations/                     # Migration runbooks (not code; alembic owns code)
│   └── README.md
└── env/
    ├── development.env.example
    ├── staging.env.example
    └── production.env.example
```

## Tools layout (`tools/`)

Developer utilities. Not deployed.

```
tools/
├── validation_report/              # The validation reporting agent
│   ├── collect_report.py           # Run after a phase ships
│   ├── template.md
│   └── README.md
├── seed_data/                      # Scripts to populate dev DB
│   └── seed_demo_company.py
└── lint/
    └── check_money_types.py        # Custom check: no float in money paths
```

## Forbidden patterns

The following structures are **explicitly forbidden**. Coder Claude does not create them, even if a task description seems to imply them:

- `backend/utils/` as a catch-all dumping ground (utilities live in `core/` or domain-specific modules)
- Top-level files outside the directories listed above (one-off scripts go to `tools/`)
- A `src/` directory inside any package (Python uses package directories directly)
- A `common/` package shared across `backend/` and `connector/` (they are independent; share via documented protocol, not code)
- Any directory with a leading space, trailing space, or special character (lesson learned from the prior repo)
- Duplicate models, services, or routers in two locations

## Module boundaries

The following imports are **forbidden** to prevent circular dependencies and architectural drift:

| Source | Cannot import from |
|---|---|
| `backend/app/models/` | `backend/app/services/`, `backend/app/api/` |
| `backend/app/schemas/` | `backend/app/services/`, `backend/app/api/` |
| `backend/app/services/` | `backend/app/api/` |
| `backend/app/core/` | `backend/app/services/`, `backend/app/api/`, `backend/app/models/` |
| `connector/` | anything under `backend/` |

These rules are enforced in CI via a static-import check (`tools/lint/check_imports.py`, to be added in Phase 0).

## Naming conventions

- **Python modules:** `snake_case.py`, no abbreviations except well-known (e.g., `db`, `pdf`, `gst`)
- **Python classes:** `PascalCase`
- **Database tables:** `snake_case`, plural (e.g., `vouchers`, `ledger_entries`)
- **Database columns:** `snake_case`, singular (e.g., `voucher_id`, `created_at`)
- **API paths:** `/api/v1/{resource-plural}` with kebab-case for multi-word resources
- **Pydantic schemas:** `{Resource}{Action}Request` / `{Resource}{Action}Response` / `{Resource}Out`
- **TypeScript types (mobile/web):** `PascalCase`
- **Files in `tests/`:** `test_{module_name}.py`, mirroring the source tree

## Phase ownership

Not every directory is populated in Phase 0. Phase ownership of subtrees:

| Subtree | Phase | Notes |
|---|---|---|
| `backend/app/core/`, `models/`, `api/v1/auth.py`, `companies.py`, `health.py` | 0 | Foundation |
| `backend/app/services/auth_service.py`, `voucher_service.py` | 0 | |
| `connector/` (skeleton + tally_client.py + ws_client.py) | 0 | Salvage + glue |
| `mobile/` (auth + dashboard + voucher CRUD only) | 0 | |
| `backend/app/services/extraction/`, `api/v1/ingestions.py` | 1 | Wedge feature |
| `mobile/src/screens/invoice_scan/` | 1 | |
| `backend/app/services/bank_statement/`, `sms/` | 2 | |
| `backend/app/services/reconciliation/` | 3 | |
| `web/` | 5 | Last; mobile-first |

Coder Claude does not create directories ahead of their phase. Empty directories are not committed.
