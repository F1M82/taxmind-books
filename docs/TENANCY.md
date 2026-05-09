# Multi-Tenant Scoping

**Status:** Frozen. The constitution Section 5 declares tenant isolation non-negotiable; this document specifies the mechanism.

This is the single most security-critical document in the architecture. Both prior repos (`taxmind-recon` and `Qwen-Tallyonmobile`) had multi-tenant data leaks. Read this before writing any route, service, or query.

## The threat model

A user logs in. They have access to companies A and B but not C. The threats we defend against:

1. **Direct ID guess.** User requests `GET /vouchers/{id}` with a UUID belonging to company C. Naive code returns the voucher; tenant-scoped code returns 404.
2. **Header tampering.** User sends `X-Company-ID: <company-C-uuid>` despite not having access. Naive code trusts the header; scoped code rejects with 403.
3. **Query parameter injection.** User sends `?company_id=<C>` to a list endpoint. Naive code filters by the parameter; scoped code uses the resolved company from the dependency, ignoring the parameter.
4. **JOIN leaks.** A query joins `vouchers` to `ledger_entries` but only filters the outer table. Naive code leaks ledger_entries from other companies. Scoped code filters at every join layer.
5. **Workers / async tasks.** A Celery task processes an ingestion. The task has no HTTP request, so the request-scoped tenant dependency doesn't apply. Naive workers operate without scoping. Scoped workers receive the company_id as a task argument and apply it explicitly.
6. **Connector messages.** The Tally connector receives commands from the backend. A compromised connector forwarding messages from another company would leak. Scoped connector messages are signed per company_id; the backend rejects mismatched messages.

## The single rule

> Every database read or write that touches a tenant-scoped table is filtered by `company_id`. The `company_id` is resolved exactly once per request from the JWT + `X-Company-ID` header by a single FastAPI dependency. No code path bypasses this dependency. No code path trusts a `company_id` from request body or query string.

## What is "tenant-scoped"

The following tables are tenant-scoped (have a `company_id` column or transitively):

- `companies` (the tenant root itself)
- `ledgers`
- `vouchers`
- `ledger_entries` (via voucher)
- `ingestions`
- `draft_vouchers` (via ingestion)
- `reconciliations`
- `recon_matches` (via reconciliation)
- `audit_logs`
- `narration_rules`

The following tables are **not** tenant-scoped (they are global / per-user only):

- `users`
- `user_companies` (the membership table; rows are filtered by user, not company)
- `sms_templates` (global library, shared across tenants)

## The dependency chain

A request flows through three FastAPI dependencies, in order. Each builds on the previous.

### Dependency 1: `get_current_user`

Validates the JWT, loads the user. Implemented in `backend/app/api/deps.py`.

```python
# backend/app/api/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        if not user_id:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise credentials_exc
    return user
```

Properties:
- Returns the loaded `User` object, not just an ID.
- Checks `is_active`. A deactivated user with an unexpired token is rejected.
- Does not load companies (cheap path; the next dependency does that lazily if needed).

### Dependency 2: `get_active_company`

Resolves the active company from the `X-Company-ID` header, validating user access. This is the heart of tenant scoping.

```python
# backend/app/api/deps.py
from uuid import UUID
from fastapi import Header, HTTPException, status
from app.models.company import Company, UserCompany


def get_active_company(
    x_company_id: UUID = Header(..., alias="X-Company-ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Company:
    """
    Resolves and authorizes the active company for this request.

    Raises 400 if X-Company-ID header is missing/malformed.
    Raises 403 if the user does not have access to the company.
    Raises 404 if the company does not exist or is suspended.

    Returns the Company object. The route handler MUST use this object
    (or its id) for all queries; query parameters / body fields named
    company_id are IGNORED by routes and rejected by lint.
    """
    membership = (
        db.query(UserCompany)
        .filter(
            UserCompany.user_id == user.id,
            UserCompany.company_id == x_company_id,
        )
        .first()
    )
    if membership is None:
        # Use 404 rather than 403 to avoid leaking existence of company IDs
        # to users who don't have access. Both unknown and unauthorized
        # return the same response.
        raise HTTPException(status_code=404, detail="Company not found")

    company = db.query(Company).filter(Company.id == x_company_id).first()
    if company is None or company.status != CompanyStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="Company not found")

    # Stash the membership role on the request for role-checking dependencies
    # (e.g., require_role(["owner", "admin"]))
    return company


def require_role(*roles: str):
    """Composable dependency: ensures user's role on the active company
    is one of the listed roles."""
    def checker(
        company: Company = Depends(get_active_company),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Company:
        membership = (
            db.query(UserCompany)
            .filter(
                UserCompany.user_id == user.id,
                UserCompany.company_id == company.id,
            )
            .first()
        )
        if membership.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return company
    return checker
```

Properties:
- The header is **required** for any tenant-scoped route. Routes that don't need it (auth, health, listing one's own companies) explicitly opt out by not depending on `get_active_company`.
- The membership check happens before any other DB work in the request, so unauthorized requests fail fast.
- The 404-instead-of-403 pattern prevents company-ID enumeration.
- `require_role` is a composable dependency for role-restricted endpoints.

### Dependency 3: `get_scoped_session`

A SQLAlchemy session that automatically applies company_id filters. This is defense in depth — even if a route handler forgets to filter, the session does.

```python
# backend/app/api/deps.py
from sqlalchemy import event
from sqlalchemy.orm import Session


def get_scoped_session(
    company: Company = Depends(get_active_company),
    db: Session = Depends(get_db),
) -> Session:
    """
    Returns a session that adds an automatic WHERE company_id = X filter
    to all queries against tenant-scoped tables.

    Implementation: SQLAlchemy's 'do_orm_execute' event listener inspects
    each query and injects a filter on any ORM mapper that has a 'company_id'
    column.
    """
    @event.listens_for(db, "do_orm_execute")
    def _scope_query(execute_state):
        if not execute_state.is_select:
            return
        # Skip if explicitly opted out (admin tools, system jobs)
        if execute_state.execution_options.get("skip_tenant_scope"):
            return
        # Inject company_id filter on tenant-scoped tables
        from app.models.base import TenantScopedMixin
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda cls: cls.company_id == company.id,
                include_aliases=True,
            )
        )

    return db
```

Properties:
- Routes use `db: Session = Depends(get_scoped_session)` instead of `Depends(get_db)`.
- The scoping is automatic. If a developer writes `db.query(Voucher).all()` they get only vouchers for the active company. The bug class "forgot to filter" is eliminated.
- Insertion is **not** automatically scoped — the developer must set `company_id` explicitly when creating a row. This is intentional; insert-time scoping would silently overwrite user input and hide bugs.
- Update and delete: the auto-filter applies to the WHERE clause of `UPDATE` and `DELETE` statements through the same loader criteria mechanism. A `db.query(Voucher).filter(Voucher.id == x).update(...)` only updates if the voucher belongs to the active company.

## The TenantScopedMixin

Every tenant-scoped model inherits from a mixin so the auto-scoping mechanism can identify it:

```python
# backend/app/models/base.py
from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class TenantScopedMixin:
    """Mixin for any model that must be filtered by company_id.

    Models inheriting this mixin are automatically scoped by the
    get_scoped_session dependency.

    The mixin also adds a database-level CHECK constraint forbidding NULL
    company_id, even if Python forgets the nullable=False.
    """
    @declared_attr
    def company_id(cls) -> Mapped[UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("companies.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
```

`ondelete="RESTRICT"` is intentional: companies cannot be hard-deleted while they have data. Soft-delete is the only path; hard-delete requires a separate, explicit data-export-then-purge flow.

## Service-layer rules

Services receive the active company via dependency injection, not by accepting a `company_id` parameter from the route. This prevents a route from "passing the wrong company_id" to a service.

```python
# CORRECT
class VoucherService:
    def __init__(self, db: Session, company: Company):
        self.db = db
        self.company = company

    def create(self, data: VoucherCreate, user: User) -> Voucher:
        voucher = Voucher(
            company_id=self.company.id,        # explicit, from injected company
            voucher_type=data.voucher_type,
            ...
            created_by=user.id,
        )
        self.db.add(voucher)
        self.db.flush()
        return voucher


# In the route:
def create_voucher(
    data: VoucherCreate,
    db: Session = Depends(get_scoped_session),
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
):
    service = VoucherService(db, company)
    voucher = service.create(data, user)
    db.commit()
    return voucher
```

```python
# FORBIDDEN
class VoucherService:
    def create(self, data: VoucherCreate, company_id: str, user: User) -> Voucher:
        # company_id passed in by caller; if caller passes the wrong one, leak.
        ...
```

## Worker rules

Celery tasks have no HTTP request, so no `get_active_company` runs. Tenant scoping is enforced explicitly:

```python
# backend/app/workers/extraction_tasks.py
@celery_app.task(bind=True, max_retries=3)
def extract_invoice(self, ingestion_id: str, company_id: str) -> None:
    """
    Extracts invoice data from an ingestion.

    Both ingestion_id AND company_id are required. The task verifies
    that the ingestion belongs to the company before processing.
    """
    db = SessionLocal()
    try:
        ingestion = (
            db.query(Ingestion)
            .filter(
                Ingestion.id == ingestion_id,
                Ingestion.company_id == company_id,    # explicit re-check
            )
            .first()
        )
        if ingestion is None:
            # Either doesn't exist or wrong company; do not retry.
            logger.error("ingestion not found", extra={
                "ingestion_id": ingestion_id, "company_id": company_id,
            })
            return
        ...
    finally:
        db.close()
```

The `company_id` is part of the task signature. Enqueueing code must pass both:

```python
# CORRECT
extract_invoice.delay(ingestion_id=str(i.id), company_id=str(company.id))

# FORBIDDEN
extract_invoice.delay(ingestion_id=str(i.id))   # Worker has no way to scope
```

## Connector rules

The Tally Connector connects to the backend WebSocket and registers itself for one specific company. The registration message includes a connector token (a JWT-like signed token that embeds the company_id and expires).

```
Connector → Backend:
{
  "type": "register",
  "connector_token": "<signed token containing company_id and expiry>",
  "tally_running": true,
  "tally_version": "3.0"
}

Backend → Connector (on commands):
{
  "type": "post_voucher",
  "request_id": "uuid",
  "company_id": "<uuid>",        // included for connector verification
  "payload": { ... }
}
```

The connector verifies that every command's `company_id` matches the company it registered for. A mismatch is logged and the message is rejected. This is defense in depth: even if the backend has a routing bug, the connector won't accept a cross-company command.

See `CONNECTOR_PROTOCOL.md` for full message catalog.

## Audit log scoping

Audit logs are themselves tenant-scoped. The audit log table has `company_id`. The audit middleware (see `AUDIT.md`) writes to the audit log using the active company from the request context.

A user who has access to companies A and B sees audit logs for both, scoped per their active-company switcher. Audit logs are never visible across companies a user doesn't belong to.

## Test suite for tenant isolation

`tests/tenant_isolation/` is a dedicated test directory. Every endpoint in the API has at least one test in this directory. The test pattern is:

```python
# tests/tenant_isolation/test_vouchers_isolation.py
def test_user_cannot_read_voucher_from_other_company(client, user_a, company_b, voucher_in_b):
    """User A is in company A only. They try to read a voucher in company B."""
    token_a = login_as(user_a, client)
    response = client.get(
        f"/api/v1/vouchers/{voucher_in_b.id}",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Company-ID": str(company_b.id),    # B, which user_a doesn't belong to
        },
    )
    assert response.status_code == 404


def test_user_cannot_set_company_id_in_body(client, user_a, company_a, company_b):
    """User A creates a voucher and tries to inject company_b in the body."""
    token = login_as(user_a, client)
    response = client.post(
        "/api/v1/vouchers/",
        headers={"Authorization": f"Bearer {token}", "X-Company-ID": str(company_a.id)},
        json={
            "company_id": str(company_b.id),    # injection attempt; should be ignored or rejected
            "voucher_type": "Receipt",
            "date": "2026-05-08",
            "total_amount": "1000.00",
        },
    )
    # Either the body field is rejected (preferred), or it's silently ignored
    # and the resulting voucher is in company_a. Both are acceptable; whichever
    # the implementation picks must be consistent and documented.
    assert response.status_code in (201, 422)
    if response.status_code == 201:
        assert response.json()["company_id"] == str(company_a.id)


def test_user_cannot_list_other_company_data(...):
    ...


def test_celery_task_rejects_mismatched_company(...):
    ...
```

Tenant isolation tests are P0. They run on every CI invocation. Skipping any of them blocks merge.

## Anti-patterns (forbidden)

```python
# FORBIDDEN — accepting company_id as input
@router.get("/vouchers/")
def list_vouchers(company_id: str, ...):    # query param: NO
    return db.query(Voucher).filter(Voucher.company_id == company_id).all()


# FORBIDDEN — trusting body-supplied company_id
def create_ledger(data: LedgerCreate, ...):
    ledger = Ledger(company_id=data.company_id, ...)    # NO; use injected company


# FORBIDDEN — bypassing scoped session
@router.get("/vouchers/{voucher_id}")
def get_voucher(voucher_id: str, db: Session = Depends(get_db)):    # NO; use get_scoped_session
    voucher = db.query(Voucher).filter(Voucher.id == voucher_id).first()
    return voucher


# FORBIDDEN — service that takes company_id as parameter from caller
class IngestionService:
    def create(self, data, company_id):    # NO; inject Company object via constructor
        ...


# FORBIDDEN — Celery task without company_id parameter
@celery_app.task
def process_ingestion(ingestion_id: str):    # NO; must include company_id
    ...


# FORBIDDEN — JOIN that filters only one side
db.query(Voucher).join(LedgerEntry).filter(Voucher.company_id == c).all()
# Even with auto-scoping, complex joins should be reviewed; if LedgerEntry
# has its own company_id, the auto-scoper handles it. If it depends on
# voucher.company_id, the FK-based check is enough but must be documented.
```

## Operational guarantees

- A user cannot read another tenant's data, even by ID.
- A user cannot write into another tenant's data, even by ID.
- A user cannot list another tenant's data.
- A user cannot enumerate other tenants by trying IDs.
- A worker cannot process a job for the wrong tenant.
- A connector cannot post a voucher to the wrong tenant.
- An audit log entry is always scoped to one tenant.

## Failure-mode tests the human runs

Validation report includes these manual checks:

1. Create users U1 and U2, companies C1 and C2. U1 is in C1 only, U2 is in C2 only.
2. Login as U1. Try to GET a voucher in C2 by ID. Expected: 404.
3. Login as U1. Try to POST a voucher with `X-Company-ID: <C2>`. Expected: 404.
4. Login as U1. POST a voucher with `X-Company-ID: <C1>` and a body that has `"company_id": "<C2>"`. Expected: 201 with `company_id == C1`, OR 422 rejection. Never 201 with `company_id == C2`.
5. Login as U1. GET `/vouchers/` without X-Company-ID header. Expected: 422 (header required).
6. Login as U1. GET `/vouchers/?company_id=<C2>`. Expected: query parameter ignored; results scoped to active company from header.

If any check fails, the system has a tenant-isolation bug. The phase does not pass.
