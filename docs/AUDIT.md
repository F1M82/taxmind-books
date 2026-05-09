# Audit Middleware

**Status:** Frozen. Constitution Section 4 declares audit trail mandatory; this document specifies the mechanism.

The audit log is the single most important *defensive* feature in this product. If a customer disputes a voucher post, the audit log decides who is right. If a contractor's code goes wrong, the audit log shows what changed. If a regulator asks how a transaction was created, the audit log answers.

This document specifies how audit logging is implemented such that no developer can write a route that bypasses it.

## The single rule

> Every state-changing operation on a tenant-scoped, financially significant entity produces exactly one audit log row, written within the same database transaction as the operation, capturing actor, target, before-state, after-state, and request context.

## What is "financially significant"

The following entities require audit logs on every CREATE, UPDATE, DELETE, or status transition:

- `Voucher` — every operation
- `LedgerEntry` — implicit via voucher (each voucher's lines audited as part of the voucher event)
- `Ledger` — every operation (master data; CA scrutiny applies)
- `ReconciliationSession` — creation, completion, and confirmation events
- `ReconciliationMatch` — confirmation/rejection events
- `Company` — creation, settings change, status change
- `UserCompany` — role assignment, role change, removal
- `User` — creation, password change, deactivation (the users table is global, not tenant-scoped, but user lifecycle events are still audited)
- `NarrationRule` — creation and modification (these affect future auto-classification)

The following entities **do not** require audit logs:

- `Ingestion` — raw inbound data; not yet a financial entity. Status transitions on the ingestion itself are not audited, but the resulting voucher's audit chain links back via the `source_ingestion_id` field.
- `DraftVoucher` — same reasoning. The transition from draft to posted voucher is audited as the voucher's create event.
- `AuditLog` — itself. Audit logs are append-only; auditing the audit log is circular.
- `SmsTemplate` — non-financial reference data.

## The audit log schema

Reference (full DDL is in SCHEMA.sql):

```sql
CREATE TABLE audit_logs (
    id            UUID PRIMARY KEY,
    company_id    UUID NOT NULL REFERENCES companies(id),
    user_id       UUID REFERENCES users(id),         -- nullable: system events
    action        VARCHAR(40) NOT NULL,               -- e.g. "voucher.created"
    entity_type   VARCHAR(40) NOT NULL,               -- e.g. "voucher"
    entity_id     UUID NOT NULL,
    old_value     JSONB,                              -- null on CREATE
    new_value     JSONB,                              -- null on DELETE
    changes       JSONB,                              -- diff of old vs new (precomputed)
    ip_address    INET,
    user_agent    TEXT,
    request_id    UUID,                               -- correlates with request log
    source        VARCHAR(20),                        -- "api", "worker", "connector", "system"
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_company_created ON audit_logs (company_id, created_at DESC);
CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX idx_audit_logs_user ON audit_logs (user_id, created_at DESC);
```

The `audit_logs` table has additional database-level protections (see *Append-only enforcement* below).

## Action vocabulary

Actions follow a strict `entity.verb` naming convention. The full v1 vocabulary:

| Action | Triggered when |
|---|---|
| `voucher.created` | New voucher inserted |
| `voucher.updated` | Voucher fields modified |
| `voucher.cancelled` | Voucher status changed to CANCELLED |
| `voucher.posted_to_tally` | Voucher successfully posted to Tally |
| `voucher.tally_post_failed` | Tally rejected the voucher |
| `voucher.posted_as_optional` | (v1.2) AI-extracted voucher posted to Tally as Optional |
| `voucher.approved_to_regular` | (v1.2) Optional voucher promoted to Regular by admin |
| `voucher.rejected_optional` | (v1.2) Optional voucher rejected and deleted from Tally |
| `ledger.created` | Ledger master created |
| `ledger.updated` | Ledger master fields modified |
| `recon.session_created` | Reconciliation session started |
| `recon.session_completed` | Recon engine finished matching |
| `recon.match_confirmed` | User confirmed a match |
| `recon.match_rejected` | User rejected a match |
| `company.created` | Company created |
| `company.settings_updated` | Company settings changed |
| `company.suspended` | Company status set to SUSPENDED |
| `user.created` | User registered |
| `user.password_changed` | Password successfully changed |
| `user.deactivated` | User is_active set to False |
| `user_company.role_assigned` | New membership |
| `user_company.role_changed` | Role changed |
| `user_company.removed` | Membership deleted |
| `narration_rule.created` | New auto-classification rule |
| `narration_rule.disabled` | Rule deactivated |
| `account.deletion_requested` | (v1.2) User requested account deletion (grace period started) |
| `account.deletion_cancelled` | (v1.2) User cancelled deletion during grace |
| `account.deletion_completed` | (v1.2) Deletion processed; user PII scrubbed |
| `data_export.requested` | (v1.2) User requested full data export |
| `data_export.completed` | (v1.2) Export bundle ready; download link sent |
| `device.registered` | (v1.2) Push notification token registered |
| `device.unregistered` | (v1.2) Push notification token deregistered |

New actions added in later phases extend this list. Coder Claude does not invent new action names; they are added to this document first.

## The mechanism

Auditing is implemented via the **service-layer pattern**, not via SQLAlchemy events or HTTP middleware. Reasoning below.

### Why not HTTP middleware

A FastAPI middleware sits at the request boundary. It sees the request body and the response body but not the database operations. To produce a useful audit row it would have to either (a) duplicate the business logic to compute old/new values, or (b) pull old values from the DB before the request and new values after. Both are fragile, both miss async work (Celery tasks have no HTTP request), both can't capture intra-request branching.

### Why not SQLAlchemy event listeners

A `before_update` / `after_insert` event fires on every commit. It captures DB-level changes. But it has no access to the actor (no current user), the request context (no IP), or the *intent* (was this an update or a status transition?). Events also fire on internal cleanup operations, producing noise. We tried this in early designs; it doesn't work.

### Why service-layer is right

The audit log is written *by the service that performed the action*, in the same DB transaction. The service knows:
- The actor (passed in via dependency)
- The intent (the method called specifies the action)
- The before-state (loaded before mutation)
- The after-state (the result of the mutation)
- The request context (passed in via the audit context object)

Services produce audit events through a single interface. The interface is mandatory; not calling it for a state-changing service method is a CI failure.

## The interface

```python
# backend/app/core/audit.py
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.user import User


@dataclass(frozen=True)
class AuditContext:
    """Per-request context passed to services for audit log emission.

    Created in the API layer (from request) and passed to services. Workers
    create their own AuditContext from task arguments.
    """
    company: Company
    user: User | None                 # None for system-initiated events
    ip_address: str | None
    user_agent: str | None
    request_id: UUID
    source: str                       # "api" | "worker" | "connector" | "system"


class AuditEmitter:
    """The single way to write audit log rows.

    Services receive an AuditEmitter; they call .emit() with the action and
    before/after snapshots. The emitter writes the row in the current DB
    session (within the caller's transaction).
    """

    def __init__(self, db: Session, ctx: AuditContext):
        self.db = db
        self.ctx = ctx

    def emit(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID,
        old_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
    ) -> AuditLog:
        if action not in _ALLOWED_ACTIONS:
            raise ValueError(f"unknown audit action: {action!r}")
        log = AuditLog(
            id=uuid4(),
            company_id=self.ctx.company.id,
            user_id=self.ctx.user.id if self.ctx.user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=_normalize_for_json(old_value),
            new_value=_normalize_for_json(new_value),
            changes=_compute_diff(old_value, new_value),
            ip_address=self.ctx.ip_address,
            user_agent=self.ctx.user_agent,
            request_id=self.ctx.request_id,
            source=self.ctx.source,
            # created_at: server default
        )
        self.db.add(log)
        # Note: NO commit here. The caller's transaction commits the audit
        # row alongside the action it is auditing. Atomicity guaranteed.
        return log


_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "voucher.created", "voucher.updated", "voucher.cancelled",
    "voucher.posted_to_tally", "voucher.tally_post_failed",
    "voucher.posted_as_optional", "voucher.approved_to_regular",
    "voucher.rejected_optional",
    "ledger.created", "ledger.updated",
    "recon.session_created", "recon.session_completed",
    "recon.match_confirmed", "recon.match_rejected",
    "company.created", "company.settings_updated", "company.suspended",
    "user.created", "user.password_changed", "user.deactivated",
    "user_company.role_assigned", "user_company.role_changed",
    "user_company.removed",
    "narration_rule.created", "narration_rule.disabled",
    "account.deletion_requested", "account.deletion_cancelled",
    "account.deletion_completed",
    "data_export.requested", "data_export.completed",
    "device.registered", "device.unregistered",
})


def _normalize_for_json(value: dict | None) -> dict | None:
    """Convert Decimal/datetime/UUID/Enum to JSON-safe primitives."""
    if value is None:
        return None
    return _walk(value)


def _walk(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)               # money: see MONEY.md
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    return obj


def _compute_diff(old: dict | None, new: dict | None) -> dict:
    """Field-level diff: { field: [old, new] } for changed keys only."""
    if old is None or new is None:
        return {}
    return {
        k: [old.get(k), new.get(k)]
        for k in set(old) | set(new)
        if old.get(k) != new.get(k)
    }
```

## How a service uses it

```python
# backend/app/services/voucher_service.py
from app.core.audit import AuditEmitter, AuditContext


class VoucherService:
    def __init__(self, db: Session, company: Company, audit: AuditEmitter):
        self.db = db
        self.company = company
        self.audit = audit

    def create(self, data: VoucherCreate, user: User) -> Voucher:
        voucher = Voucher(
            company_id=self.company.id,
            voucher_type=data.voucher_type,
            date=data.date,
            total_amount=data.total_amount,
            narration=data.narration,
            created_by=user.id,
            status=VoucherStatus.POSTED,
        )
        self.db.add(voucher)
        self.db.flush()                          # populate voucher.id

        self.audit.emit(
            action="voucher.created",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value=_voucher_snapshot(voucher),
        )
        return voucher

    def cancel(self, voucher_id: UUID, user: User) -> Voucher:
        voucher = self._load_voucher(voucher_id)
        old_snap = _voucher_snapshot(voucher)
        voucher.status = VoucherStatus.CANCELLED
        self.db.flush()
        new_snap = _voucher_snapshot(voucher)

        self.audit.emit(
            action="voucher.cancelled",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=old_snap,
            new_value=new_snap,
        )
        return voucher
```

The service is responsible for:
1. Loading the entity before mutation (for `old_value`).
2. Performing the mutation.
3. Calling `audit.emit` with the appropriate action.
4. Letting the route commit the DB transaction. Audit and action commit atomically.

## How the API wires it

```python
# backend/app/api/deps.py
from app.core.audit import AuditContext, AuditEmitter

def get_audit_context(
    request: Request,
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
) -> AuditContext:
    return AuditContext(
        company=company,
        user=user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=UUID(request.headers.get("X-Request-ID", str(uuid4()))),
        source="api",
    )

def get_audit_emitter(
    db: Session = Depends(get_scoped_session),
    ctx: AuditContext = Depends(get_audit_context),
) -> AuditEmitter:
    return AuditEmitter(db, ctx)


# backend/app/api/v1/vouchers.py
@router.post("/", status_code=201)
def create_voucher(
    data: VoucherCreate,
    db: Session = Depends(get_scoped_session),
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    audit: AuditEmitter = Depends(get_audit_emitter),
):
    service = VoucherService(db, company, audit)
    voucher = service.create(data, user)
    db.commit()                                  # action + audit commit together
    return voucher
```

## How workers wire it

Workers don't have a request. They construct `AuditContext` from task arguments:

```python
# backend/app/workers/posting_tasks.py
@celery_app.task(bind=True, max_retries=5)
def post_voucher_to_tally(
    self,
    voucher_id: str,
    company_id: str,
    user_id: str | None,
    request_id: str,
):
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        user = db.query(User).filter(User.id == user_id).first() if user_id else None
        ctx = AuditContext(
            company=company,
            user=user,
            ip_address=None,
            user_agent="celery-worker/1.0",
            request_id=UUID(request_id),
            source="worker",
        )
        audit = AuditEmitter(db, ctx)
        service = VoucherService(db, company, audit)
        try:
            service.post_to_tally(UUID(voucher_id))
        except TallyError as e:
            audit.emit(
                action="voucher.tally_post_failed",
                entity_type="voucher", entity_id=UUID(voucher_id),
                old_value=None,
                new_value={"error": str(e), "retry_attempt": self.request.retries},
            )
            db.commit()
            raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
        db.commit()
    finally:
        db.close()
```

The `request_id` originates in the API request that triggered the work; it's preserved through the queue so audit logs across API and worker correlate.

## Append-only enforcement

The `audit_logs` table is **append-only**. Three layers of enforcement:

### Layer 1: database privileges

The application connects as a role (`taxmind_app`) that has only `INSERT` and `SELECT` on `audit_logs`. No UPDATE, no DELETE.

```sql
-- ops/migrations/grants.sql (run after migrations as DB admin)
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM taxmind_app;
GRANT INSERT, SELECT ON audit_logs TO taxmind_app;
```

This is set up in the production deployment runbook. CI verifies the privilege configuration during integration tests.

### Layer 2: trigger

A PG trigger raises an exception on UPDATE or DELETE attempts, regardless of role:

```sql
CREATE OR REPLACE FUNCTION prevent_audit_modification() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_logs_no_update BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER audit_logs_no_delete BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
```

### Layer 3: code

The ORM model `AuditLog` is read-only via class-level configuration (no setters generated). The `AuditEmitter` is the only path that constructs `AuditLog` instances. CI lints for any code outside `core/audit.py` constructing `AuditLog`.

## Mandatory-emit enforcement

A service method that mutates a financially significant entity must call `audit.emit`. This is enforced by:

1. **Convention.** Services receive `AuditEmitter` via constructor injection. Methods that don't mutate state don't need it (read-only services). Methods that mutate state get it. This makes the absence visible in code review.

2. **CI lint.** `tools/lint/check_audit_emit.py` is an AST-based check. It scans `app/services/`. For each method that:
   - Calls `db.add()`, `db.delete()`, or assigns to a model attribute on a known financial entity
   - And does not call `self.audit.emit(...)`
   
   The check fails CI. False positives are suppressed via an explicit `# audit-exempt: <reason>` comment, which the reviewer must approve.

3. **Integration tests.** Every state-changing endpoint has a corresponding test that asserts an audit log row was written:
   ```python
   def test_create_voucher_writes_audit_log(client, auth_headers, db):
       response = client.post("/api/v1/vouchers/", json=..., headers=auth_headers)
       assert response.status_code == 201
       voucher_id = response.json()["id"]
       
       audit = db.query(AuditLog).filter(
           AuditLog.entity_type == "voucher",
           AuditLog.entity_id == voucher_id,
           AuditLog.action == "voucher.created",
       ).first()
       assert audit is not None
       assert audit.user_id == TEST_USER_ID
       assert audit.new_value["total_amount"] == "1500.00"
   ```

## Snapshot rules

The `old_value` and `new_value` JSON snapshots are *complete* representations of the entity, not deltas. Reasoning: a future query asking "what did this voucher look like at 2026-08-15" should be answerable from the most recent audit log row before that timestamp without reconstructing state from a chain of diffs.

Snapshot helpers per entity live next to the model:

```python
# backend/app/models/voucher.py
def _voucher_snapshot(v: Voucher) -> dict:
    return {
        "id": str(v.id),
        "company_id": str(v.company_id),
        "voucher_type": v.voucher_type.value,
        "voucher_number": v.voucher_number,
        "date": v.date.isoformat(),
        "narration": v.narration,
        "reference": v.reference,
        "total_amount": str(v.total_amount),
        "status": v.status.value,
        "source": v.source,
        "is_auto_posted": v.is_auto_posted,
        "confidence_score": v.confidence_score,
        "gst_applicable": v.gst_applicable,
        "cgst": str(v.cgst) if v.cgst else None,
        "sgst": str(v.sgst) if v.sgst else None,
        "igst": str(v.igst) if v.igst else None,
        "tds_amount": str(v.tds_amount) if v.tds_amount else None,
        "tds_section": v.tds_section,
        # ledger entries:
        "entries": [_ledger_entry_snapshot(e) for e in v.entries],
    }
```

Snapshots are deterministic — the same entity produces the same JSON. Field order is fixed (Python 3.7+ dict order). Money is serialized as string per `MONEY.md`.

## Sensitive-data handling

The audit log can contain sensitive data (party names, GSTINs, amounts). Two protections:

1. **No passwords, no tokens, no API keys ever appear in audit values.** The `_normalize_for_json` helper redacts any key matching `password`, `secret`, `token`, `api_key`, replacing the value with `"***REDACTED***"`. This is a defense in depth — audit rows for the user-password-change action don't include the password in the snapshot to begin with, but the redactor catches accidents.

2. **PII visibility.** Audit logs are visible only to users with role `owner` or `admin` on the company. Other roles cannot query the audit log API. The route `GET /api/v1/audit-logs/` uses `Depends(require_role("owner", "admin"))`.

## Read API

```
GET /api/v1/audit-logs/
  Query params:
    entity_type?: string
    entity_id?: uuid
    user_id?: uuid
    action?: string
    from?: ISO8601 timestamp
    to?: ISO8601 timestamp
    limit?: int (default 50, max 200)
    cursor?: string (opaque pagination cursor)
  Response: paginated list of audit log entries, scoped to active company
  Required role: owner | admin
```

The audit log read API is a v1 feature. CAs and customer admins use it for review.

## Performance considerations

- Audit log writes are in the same transaction as the action, so they are blocking. This is intentional (atomicity > throughput). Single-action latency overhead: ~1-3ms per row.
- The audit log table is partitioned by month in production (`PARTITION BY RANGE (created_at)`). This is set up in the deployment runbook; not in the application migrations.
- Indices on `(company_id, created_at DESC)`, `(entity_type, entity_id)`, `(user_id, created_at DESC)` cover the common query patterns.
- Old partitions can be archived to S3 Glacier after 1 year (still queryable on demand). Retention is 8 years per architecture doc.

## Failure-mode tests the human runs

Validation report includes these manual checks:

1. Create a voucher via API. Query `audit_logs` directly. Verify exactly one row exists with `action='voucher.created'`, the user_id matches, `new_value` JSON contains the voucher fields including `total_amount` as string.
2. Update the voucher's narration. Verify a second row with `action='voucher.updated'`, `old_value.narration` is the original, `new_value.narration` is the new value, `changes` contains only the narration field.
3. Cancel the voucher. Verify a third row with `action='voucher.cancelled'`.
4. Run `UPDATE audit_logs SET action='tampered' WHERE id = ...` as the application DB user. Expected: permission denied, OR trigger raises exception. Either is acceptable; both is preferred.
5. Force a Tally post failure (disconnect the connector). Verify an audit row with `action='voucher.tally_post_failed'`, `new_value.error` populated, `source='worker'`, and the worker's `request_id` matches the API request that triggered the post.
6. Login as a `viewer` role user. GET `/api/v1/audit-logs/`. Expected: 403.
7. Verify no audit log row contains a password, token, or API key in its JSON values.

If any check fails, the audit subsystem has a defect. The phase does not pass.
