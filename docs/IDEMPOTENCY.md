# Idempotency

**Status:** Frozen.

In a financial system, exactly-once delivery of state-changing operations is a hard requirement. Network retries, client-side double-taps, mobile app reconnects after a tunnel break — all produce the same logical request being submitted multiple times. Without idempotency, the customer ends up with duplicate vouchers, duplicate payments, duplicate entries.

This document defines the contract between clients and the backend that prevents duplicates without requiring clients to reason about distributed systems.

## The single rule

> Every state-changing financial endpoint accepts an `Idempotency-Key` header. The first request with a given key creates the resource and stores the response. Every subsequent request with the same key (within the dedup window) returns the same response without re-executing the operation. Replays with mismatched bodies are rejected.

## Header

```
Idempotency-Key: <client-generated-string>
```

Format constraints:
- ASCII printable, no whitespace
- Length 8–255 characters
- Recommended: UUID v4 (clients should use this unless they have a reason not to)

## Where idempotency is required

`Idempotency-Key` is **required** (request fails with 400 if missing) on:

| Endpoint | Why |
|---|---|
| `POST /api/v1/vouchers/` | Creates a financial entry |
| `POST /api/v1/draft-vouchers/{id}/approve` | Creates a financial entry |
| `POST /api/v1/ingestions/` | Creates work; uploaded files cost money to process |
| `POST /api/v1/reconciliations/upload` | Kicks off async work and may post adjustment entries |
| `POST /api/v1/connector/sync/{company_id}` | Triggers Tally master sync |

`Idempotency-Key` is **supported but optional** on all other state-changing endpoints (PATCH, DELETE on financial entities, member adds, etc.). Clients are encouraged to send it; the server will use it if present.

GET endpoints do not accept Idempotency-Key (idempotent by HTTP semantics).

## Lifecycle

```
Client sends POST with Idempotency-Key=K
        │
        ▼
Backend looks up (company_id, K) in idempotency_keys table
        │
        ├─── Not found ────────────────────────────────────┐
        │                                                  │
        │                                                  ▼
        │                                          INSERT row with
        │                                          status=in_progress,
        │                                          locked_at=NOW(),
        │                                          request_hash=H(body)
        │                                                  │
        │                                                  ▼
        │                                          Execute the request
        │                                                  │
        │                                                  ▼
        │                                          UPDATE row with
        │                                          response_status,
        │                                          response_body,
        │                                          response_headers,
        │                                          completed_at=NOW()
        │                                                  │
        │                                                  ▼
        │                                          Return response
        │
        ├─── Found, status=in_progress, locked_at recent ──────────┐
        │                                                          ▼
        │                                                  Return 409 idempotency_in_progress
        │                                                  with Retry-After header
        │
        ├─── Found, status=completed, request_hash matches ────────┐
        │                                                          ▼
        │                                                  Return stored response
        │                                                  (status, body, headers)
        │
        └─── Found, request_hash MISMATCH ─────────────────────────┐
                                                                   ▼
                                                           Return 409 idempotency_replay
                                                           with details
```

## Storage

Idempotency keys are stored in the `idempotency_keys` Postgres table (see `SCHEMA.sql`):

```sql
CREATE TABLE idempotency_keys (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id           UUID REFERENCES users(id) ON DELETE CASCADE,
    key               VARCHAR(255) NOT NULL,
    method            VARCHAR(10) NOT NULL,
    path              VARCHAR(500) NOT NULL,
    request_hash      VARCHAR(64) NOT NULL,
    response_status   INTEGER,
    response_body     JSONB,
    response_headers  JSONB,
    locked_at         TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_idempotency_keys_company_key UNIQUE (company_id, key)
);
```

**Why Postgres, not Redis:**
- Atomicity. The idempotency-key insert and the resource create must be in the same DB transaction. Two-phase commit across Redis and Postgres is not justified for this use case.
- Persistence. Customers retrying 36 hours later (e.g., across a long offline period) should still get the deduplication. Redis has TTL eviction; Postgres has explicit expiry we control.
- Auditability. The idempotency table is itself an audit-able record of every state-changing request.

The trade-off is throughput. We accept it. In v1 the system handles tens of requests per second per company; the Postgres write is not the bottleneck.

## Scope

Keys are scoped to `(company_id, key)`. Two different companies using the same string `"abc"` do not collide.

Keys are **not** scoped to user or endpoint. The same key on `POST /vouchers/` and `POST /ingestions/` would collide. This is intentional: clients should use UUID v4, where collisions are impossible. If a client uses a deterministic key (e.g., `voucher-2026-05-08-001`), the responsibility for uniqueness is theirs.

## Dedup window

Keys expire **24 hours** after the original request. After expiry, the same key may be reused (and gets a new dedup window starting from the new request).

Rationale: 24h covers the worst realistic retry scenario (mobile offline overnight, reconnects in the morning) without making the table grow unboundedly.

A daily cleanup job deletes expired rows:

```sql
DELETE FROM idempotency_keys WHERE expires_at < NOW();
```

This runs as a Celery beat task every hour. The backend never reads expired rows; the cleanup is for storage hygiene.

## Request hash

The `request_hash` is `SHA-256(canonical_body)` — a hex string. It detects "same key, different body" replays.

Canonical body construction:
1. Parse the request body as JSON (or empty `{}` if no body).
2. Sort keys recursively.
3. Serialize with no whitespace and stable formatting (`json.dumps(canonical, sort_keys=True, separators=(',', ':'))`).
4. SHA-256 the UTF-8 bytes.

Headers are NOT part of the hash. Method and path are stored but not hashed (mismatch on those is treated as a `key_misuse` error rather than a replay collision).

## Lock semantics

Between insert and completion, the row's `locked_at` indicates "in progress." A second request arriving in the gap (say, the client retried within 50ms) will:

1. Try to insert (fails on uniqueness).
2. SELECT the row, sees `locked_at` recent (< 60s), `completed_at` null.
3. Returns 409 `idempotency_in_progress` with `Retry-After: 5` header.

If `locked_at` is older than 60 seconds and `completed_at` is still null, we assume the original request crashed mid-execution. The second request is allowed to proceed (the dead row is updated with the new attempt's lock).

This means: in pathological cases (server crash mid-transaction), exactly-once is downgraded to "at most once + at least once = exactly one or zero, never two." Safer than two.

## Response replay

When a completed key is replayed with a matching hash, the stored response is returned verbatim:

- HTTP status from `response_status`
- JSON body from `response_body`
- `Idempotent-Replay: true` header added (client can detect replay if it cares)
- Original headers from `response_headers` (Set-Cookie, Location, etc.)

The response is byte-for-byte the same as the first response — including the `created_at` timestamps in the body, which reflect the **original** request time, not the replay time. This is correct: the resource was created at the original time.

## Hash mismatch

When the same key appears with a different body, the server returns:

```json
{
  "error": {
    "code": "idempotency_replay",
    "message": "Idempotency-Key was used previously with a different request body. Use a new key for new requests.",
    "details": {
      "original_request_at": "2026-05-08T10:00:00+05:30",
      "original_path": "/api/v1/vouchers/"
    }
  },
  "request_id": "uuid"
}
```

HTTP status `409`.

This is almost always a client bug — the client reused a key by accident. The detail block helps the client developer diagnose.

## Method/path mismatch

When the same key appears with a different method or path:

```json
{
  "error": {
    "code": "idempotency_key_misuse",
    "message": "Idempotency-Key was previously used on a different endpoint.",
    "details": {
      "original_method": "POST",
      "original_path": "/api/v1/vouchers/"
    }
  }
}
```

HTTP status `409`. Same as hash mismatch — almost always a client bug.

## Implementation

The middleware lives in `backend/app/core/idempotency.py`:

```python
# backend/app/core/idempotency.py
import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.models.idempotency_key import IdempotencyKey

DEDUP_WINDOW = timedelta(hours=24)
LOCK_TIMEOUT = timedelta(seconds=60)


class IdempotencyHandler:
    """Per-request idempotency check.

    Used as a FastAPI dependency on routes that require/support idempotency.
    """

    def __init__(self, request: Request, db: Session, company_id: UUID, user_id: UUID):
        self.request = request
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.key = request.headers.get("Idempotency-Key")
        self._row: IdempotencyKey | None = None

    async def check(self, *, required: bool) -> Response | None:
        """Returns a Response if this is a replay; None if proceed.

        Raises HTTPException for missing-key or bad-body cases.
        """
        if not self.key:
            if required:
                raise HTTPException(status_code=400, detail={
                    "error": {"code": "idempotency_key_required",
                              "message": "Idempotency-Key header is required for this endpoint."}
                })
            return None

        if not _is_valid_key(self.key):
            raise HTTPException(status_code=400, detail={
                "error": {"code": "idempotency_key_invalid",
                          "message": "Idempotency-Key must be 8-255 ASCII printable, no whitespace."}
            })

        body_bytes = await self.request.body()
        request_hash = _canonical_hash(body_bytes)

        # Try to insert. If exists, fetch existing.
        # We use INSERT ... ON CONFLICT DO NOTHING RETURNING to be atomic.
        existing = (
            self.db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.company_id == self.company_id,
                IdempotencyKey.key == self.key,
            )
            .with_for_update()                  # row lock for the lock-window check
            .first()
        )

        if existing is None:
            # First time we see this key.
            row = IdempotencyKey(
                company_id=self.company_id,
                user_id=self.user_id,
                key=self.key,
                method=self.request.method,
                path=self.request.url.path,
                request_hash=request_hash,
                locked_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + DEDUP_WINDOW,
            )
            self.db.add(row)
            self.db.flush()
            self._row = row
            return None

        # Validate method/path match
        if existing.method != self.request.method or existing.path != self.request.url.path:
            raise HTTPException(status_code=409, detail={
                "error": {
                    "code": "idempotency_key_misuse",
                    "message": "Idempotency-Key was previously used on a different endpoint.",
                    "details": {
                        "original_method": existing.method,
                        "original_path": existing.path,
                    },
                }
            })

        # In progress?
        if existing.completed_at is None:
            if existing.locked_at and (datetime.utcnow() - existing.locked_at) < LOCK_TIMEOUT:
                raise HTTPException(status_code=409, detail={
                    "error": {
                        "code": "idempotency_in_progress",
                        "message": "A request with this Idempotency-Key is already in progress.",
                    }
                }, headers={"Retry-After": "5"})
            # Stale lock — assume previous attempt died, take over
            existing.locked_at = datetime.utcnow()
            existing.request_hash = request_hash
            self.db.flush()
            self._row = existing
            return None

        # Completed: check hash
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail={
                "error": {
                    "code": "idempotency_replay",
                    "message": "Idempotency-Key was used previously with a different request body. "
                               "Use a new key for new requests.",
                    "details": {
                        "original_request_at": existing.completed_at.isoformat(),
                        "original_path": existing.path,
                    },
                }
            })

        # Replay: return stored response
        return Response(
            content=json.dumps(existing.response_body),
            status_code=existing.response_status,
            headers={
                **(existing.response_headers or {}),
                "Idempotent-Replay": "true",
                "Content-Type": "application/json",
            },
        )

    def store_response(self, status_code: int, body: dict, headers: dict | None = None) -> None:
        """Called after the route completes. Stores the response for future replays."""
        if self._row is None:
            return
        self._row.response_status = status_code
        self._row.response_body = body
        self._row.response_headers = headers or {}
        self._row.completed_at = datetime.utcnow()
        # locked_at remains; it's informational after completion


def _is_valid_key(key: str) -> bool:
    return (
        8 <= len(key) <= 255
        and all(c.isprintable() and not c.isspace() and ord(c) < 128 for c in key)
    )


def _canonical_hash(body: bytes) -> str:
    """SHA-256 of canonical JSON form."""
    if not body:
        return hashlib.sha256(b"{}").hexdigest()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        # Non-JSON body (multipart, etc.): hash the raw bytes
        return hashlib.sha256(body).hexdigest()
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

## Wiring into routes

Routes that require idempotency declare a dependency:

```python
# backend/app/api/v1/vouchers.py
from app.core.idempotency import IdempotencyHandler, get_idempotency_handler


@router.post("/", status_code=201)
async def create_voucher(
    data: VoucherCreate,
    response: Response,
    db: Session = Depends(get_scoped_session),
    company: Company = Depends(get_active_company),
    user: User = Depends(get_current_user),
    audit: AuditEmitter = Depends(get_audit_emitter),
    idem: IdempotencyHandler = Depends(get_idempotency_handler),
):
    # Check for replay
    replay_response = await idem.check(required=True)
    if replay_response is not None:
        return replay_response

    # Execute the request
    service = VoucherService(db, company, audit)
    voucher = service.create(data, user)
    db.commit()

    # Store response for future replays
    voucher_dict = VoucherOut.model_validate(voucher).model_dump(mode="json")
    idem.store_response(status_code=201, body=voucher_dict)
    db.commit()                           # commit the stored response

    return voucher_dict
```

Both commits happen atomically because they share the same DB session. If the operation fails before `store_response`, the entire transaction rolls back including the idempotency row — the next retry sees no row and proceeds.

## Multipart requests

For multipart endpoints (`POST /ingestions/` with file upload), the canonical hash is computed over the entire raw body bytes. This works because multipart boundaries are deterministic per request when the client uses the same boundary.

A second retry with the *same file* but different multipart boundary would produce different bytes and different hash, falsely classified as a body mismatch. Clients are advised: when retrying, re-send the **identical** multipart body, byte-for-byte. Mobile and web clients that use Idempotency-Key store the encoded body in their retry buffer, not just the file.

## Workers and connector messages

Idempotency at the Celery / connector layer is separate. Each task accepts a `task_idempotency_key` argument; the worker checks it against a Redis set before processing. This is documented in `CONNECTOR_PROTOCOL.md` for connector messages and in workers' inline docs. The HTTP idempotency in this document does not apply to internal queues.

## Failure modes

| Scenario | Behavior |
|---|---|
| Network drop after send, before receive | Client retries with same key → gets stored response (or in_progress if quick) |
| Client sends without key on required endpoint | 400 idempotency_key_required |
| Client double-taps button (50ms apart) | First request creates, second hits in_progress 409 with Retry-After |
| Same key on two different endpoints | 409 idempotency_key_misuse |
| Same key with mutated body | 409 idempotency_replay |
| Server crashes mid-transaction | Row exists with locked_at but null completed_at; after 60s the lock is considered stale; next retry takes over and proceeds |
| Same key after 24h | Treated as a fresh key; new resource created |
| Body too large to hash | The canonical_hash function streams; hash works for any size up to the multipart upload limit (10MB) |

## Test cases the human runs during validation

Validation report includes:

1. POST a voucher with `Idempotency-Key: K1`. Expected: 201, voucher created.
2. POST the same body with the same `K1`. Expected: 201, **same response body** (same voucher.id), `Idempotent-Replay: true` header present.
3. POST a different body with `K1`. Expected: 409 `idempotency_replay`.
4. POST a voucher without `Idempotency-Key` header. Expected: 400 `idempotency_key_required`.
5. POST with `Idempotency-Key: <space chars>`. Expected: 400 `idempotency_key_invalid`.
6. POST with `Idempotency-Key: K2` on `/vouchers/`. Then POST with `K2` on `/ingestions/`. Expected: second request returns 409 `idempotency_key_misuse`.
7. Verify exactly one row exists in `idempotency_keys` table after step 1. Verify exactly one voucher row.
8. After 24h (simulated by aging the row's expires_at via SQL), POST with `K1` again. Expected: 201, **new** voucher created.
9. Database query: `SELECT COUNT(*) FROM vouchers WHERE created_by = U` matches the count of distinct successful POSTs (not retries).

If any check fails, the idempotency subsystem has a defect. The phase does not pass.
