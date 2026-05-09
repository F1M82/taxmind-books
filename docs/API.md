# API Contracts

**Status:** Frozen for Phase 0 and Phase 1 endpoints. Later-phase endpoints added to this document before implementation.

This document is the contract between the backend and clients (mobile, web, future integrations). Coder Claude implements routes against these contracts. The mobile app's TypeScript API client is generated from / matches these schemas.

## Conventions

### Base URL

`/api/v1` is the prefix for all endpoints. v2 (when needed) gets `/api/v2` and v1 routes remain accessible until deprecated.

### Authentication

JSON Web Token (JWT) bearer authentication. The token is obtained from `POST /api/v1/auth/login` and sent in the `Authorization` header on subsequent requests:

```
Authorization: Bearer <token>
```

Routes that don't require authentication explicitly say so. All others require a valid bearer token.

### Tenant scoping

Tenant-scoped routes require the `X-Company-ID` header. See `TENANCY.md`. Routes that don't accept `X-Company-ID` (auth, health, listing one's own companies) are explicitly noted.

### Idempotency

State-changing endpoints (POST, PUT, PATCH, DELETE on financial entities) accept an `Idempotency-Key` header. See `IDEMPOTENCY.md`. The header is **required** on:

- `POST /api/v1/vouchers/`
- `POST /api/v1/ingestions/`
- `POST /api/v1/reconciliations/upload`
- `POST /api/v1/connector/sync/{company_id}`

It is **optional but supported** on all other state-changing endpoints. Where required, the request returns 400 if the header is missing.

### Request ID

Every request may carry an `X-Request-ID` header (UUID). If absent, the server generates one. It is echoed in the response and propagated to logs and audit trail. Clients should generate one per logical operation.

### Standard response envelope

There is no envelope. Responses return the resource directly:

```json
{
  "id": "...",
  "field": "value"
}
```

Lists return an object with `items` and pagination metadata:

```json
{
  "items": [ {...}, {...} ],
  "meta": {
    "next_cursor": "opaque-string-or-null",
    "total": 142
  }
}
```

Money is always serialized as **string** (see `MONEY.md`). All other JSON conventions: ISO 8601 datetimes, UUIDs as strings, enums as strings.

### Standard error envelope

Errors always return:

```json
{
  "error": {
    "code": "voucher_not_found",
    "message": "Human-readable description.",
    "details": { ... }
  },
  "request_id": "uuid"
}
```

`code` is a stable machine-readable identifier. Clients switch on it. `message` is user-displayable. `details` is optional extra context (e.g. validation errors).

### HTTP status codes

| Status | Meaning |
|---|---|
| 200 | Success (GET, PUT, PATCH) |
| 201 | Resource created (POST) |
| 202 | Accepted, async work in progress (returns task ID) |
| 204 | Success, no content (DELETE) |
| 400 | Bad request (malformed JSON, missing required header) |
| 401 | Authentication required or token invalid |
| 403 | Authenticated but insufficient role |
| 404 | Resource not found OR not authorized (per TENANCY.md, both return 404) |
| 409 | Conflict (idempotency clash, voucher number collision, etc.) |
| 422 | Validation error (Pydantic) |
| 429 | Rate limit exceeded |
| 500 | Server error (logged, never exposes internals) |
| 502 | Upstream error (Tally connector, AI provider) |
| 503 | Service unavailable (Tally connector offline, etc.) |

### Pagination

Cursor-based. List endpoints accept `limit` (default 50, max 200) and `cursor` (opaque). Response includes `meta.next_cursor`. When `next_cursor` is null, the list is complete.

### Validation errors (422)

Pydantic validation errors are surfaced as:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request body failed validation.",
    "details": {
      "errors": [
        { "loc": ["body", "total_amount"], "msg": "money values must not be float", "type": "value_error" }
      ]
    }
  },
  "request_id": "uuid"
}
```

---

## Phase 0 Endpoints

### Authentication

#### `POST /api/v1/auth/register`

Register a new user. Does not require auth.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "min-12-chars",
  "full_name": "Gaurav Chandaliya",
  "phone": "+919876543210",
  "is_ca": true,
  "firm_name": "Chandaliya & Co",
  "ca_membership_no": "M123456"
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "full_name": "Gaurav Chandaliya",
  "is_ca": true,
  "firm_name": "Chandaliya & Co",
  "is_active": true,
  "created_at": "2026-05-08T10:00:00+05:30"
}
```

**Errors:**
- `409 email_already_registered` — email exists
- `422 validation_error` — password too short, malformed phone, etc.

**Constraints:**
- Email is lowercased before storage and uniqueness check.
- Password minimum 12 characters; bcrypt-hashed.
- `phone` must match `^[+]?[0-9]{10,15}$`.

#### `POST /api/v1/auth/login`

Exchange credentials for tokens. Does not require auth.

**Request:** Form-encoded (OAuth2 password flow):
```
username=user@example.com&password=secret
```

**Response 200:**
```json
{
  "access_token": "jwt",
  "refresh_token": "jwt",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "Gaurav Chandaliya",
    "is_ca": true,
    "firm_name": "Chandaliya & Co"
  }
}
```

**Errors:**
- `401 invalid_credentials` — wrong email or password (do not distinguish)
- `403 user_inactive` — user.is_active = false

**Constraints:**
- Access token expires in 30 minutes.
- Refresh token expires in 7 days.

#### `POST /api/v1/auth/refresh`

Exchange a refresh token for a new access token. Does not require bearer auth (uses refresh token from body).

**Request:**
```json
{ "refresh_token": "jwt" }
```

**Response 200:** same shape as `/login` response.

**Errors:**
- `401 invalid_refresh_token` — expired or malformed

#### `GET /api/v1/auth/me`

Returns the authenticated user. Requires auth. Does not require `X-Company-ID`.

**Response 200:**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "full_name": "Gaurav Chandaliya",
  "is_ca": true,
  "firm_name": "Chandaliya & Co",
  "is_active": true,
  "companies": [
    {
      "id": "uuid",
      "name": "Acme Traders",
      "role": "owner"
    }
  ]
}
```

`companies` is the list of companies the user has access to, used by the mobile app's company switcher.

#### `POST /api/v1/auth/password`

Change password. Requires auth.

**Request:**
```json
{
  "current_password": "...",
  "new_password": "..."
}
```

**Response 204** on success.

**Errors:**
- `401 invalid_credentials` — current password wrong
- `422 validation_error` — new password too short

---

### Companies

#### `POST /api/v1/companies/`

Create a company. Caller becomes its owner.

Requires auth. Does not require `X-Company-ID`.

**Request:**
```json
{
  "name": "Acme Traders",
  "gstin": "27AAAAA1234A1Z5",
  "pan": "AAAAA1234A",
  "financial_year_start": "2026-04-01",
  "address": "123 Main St",
  "city": "Nagpur",
  "state_code": "27",
  "pincode": "440001",
  "accounting_source": "tally"
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "name": "Acme Traders",
  "gstin": "27AAAAA1234A1Z5",
  "pan": "AAAAA1234A",
  "financial_year_start": "2026-04-01",
  "status": "active",
  "address": "123 Main St",
  "city": "Nagpur",
  "state_code": "27",
  "pincode": "440001",
  "accounting_source": "tally",
  "created_at": "2026-05-08T10:00:00+05:30",
  "your_role": "owner"
}
```

**Errors:**
- `409 gstin_already_registered`
- `422 validation_error` — invalid GSTIN/PAN/pincode format

#### `GET /api/v1/companies/`

List companies the caller has access to. Requires auth. Does not require `X-Company-ID`.

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "Acme Traders",
      "gstin": "27AAAAA1234A1Z5",
      "status": "active",
      "your_role": "owner"
    }
  ],
  "meta": { "next_cursor": null, "total": 1 }
}
```

#### `GET /api/v1/companies/{company_id}`

Get a single company. Caller must have access.

Requires auth. Does not require `X-Company-ID` header (the path param is the scope).

**Response 200:** same shape as POST 201.

**Errors:**
- `404 company_not_found` — company doesn't exist OR caller has no access

#### `PATCH /api/v1/companies/{company_id}`

Update company settings. Requires `owner` or `admin` role.

**Request:** any subset of mutable fields:
```json
{
  "name": "Acme Trading Co",
  "address": "...",
  "accounting_source": "tally"
}
```

**Response 200:** updated company.

**Errors:**
- `403 insufficient_role`
- `404 company_not_found`

#### `POST /api/v1/companies/{company_id}/members`

Invite or add a user to a company. Requires `owner` role.

**Request:**
```json
{
  "email": "accountant@example.com",
  "role": "accountant"
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "company_id": "uuid",
  "role": "accountant",
  "user_email": "accountant@example.com",
  "created_at": "..."
}
```

**Errors:**
- `403 insufficient_role`
- `404 user_not_found` — email doesn't correspond to a registered user (v1 requires the invitee to register first; invite-via-email flow is Phase 5)
- `409 already_member`

---

### Ledgers

All ledger endpoints require auth and `X-Company-ID`.

#### `GET /api/v1/ledgers/`

List ledgers for the active company.

**Query parameters:**
- `group?: string` — filter by group_name
- `is_active?: boolean` — default true
- `q?: string` — fuzzy search by name (uses pg_trgm)
- `limit?: int`, `cursor?: string`

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "Sharma Traders",
      "group_name": "Sundry Debtors",
      "opening_balance": "0.00",
      "balance_type": "Dr",
      "gstin": "27BBBBB5678B1Z5",
      "is_active": true
    }
  ],
  "meta": { "next_cursor": null, "total": 142 }
}
```

#### `POST /api/v1/ledgers/`

Create a ledger.

**Request:**
```json
{
  "name": "Sharma Traders",
  "group_name": "Sundry Debtors",
  "opening_balance": "0.00",
  "balance_type": "Dr",
  "gstin": "27BBBBB5678B1Z5",
  "phone": "+919876543210",
  "address": "...",
  "state_code": "27"
}
```

**Response 201:** ledger object.

#### `GET /api/v1/ledgers/{ledger_id}`

Get a ledger.

**Response 200:** ledger object with all fields.

#### `PATCH /api/v1/ledgers/{ledger_id}`

Update mutable fields.

#### `DELETE /api/v1/ledgers/{ledger_id}`

Soft-delete (sets `is_active = false`). Hard delete is forbidden.

**Response 204** on success.

**Errors:**
- `409 ledger_in_use` — ledger has voucher entries; cannot deactivate without explicit confirm flag
- `409 ledger_in_use` with `details.entry_count` returned

---

### Vouchers

All voucher endpoints require auth and `X-Company-ID`.

#### `POST /api/v1/vouchers/`

Create a voucher. **Idempotency-Key required.**

**Request:**
```json
{
  "voucher_type": "Receipt",
  "date": "2026-05-08",
  "narration": "Payment received from Sharma Traders",
  "reference": "UTR1234567",
  "total_amount": "50000.00",
  "entries": [
    {
      "ledger_id": "uuid-of-bank-ledger",
      "amount": "50000.00",
      "entry_type": "Dr"
    },
    {
      "ledger_id": "uuid-of-sharma-ledger",
      "amount": "50000.00",
      "entry_type": "Cr"
    }
  ],
  "gst_applicable": false,
  "place_of_supply": null
}
```

**Response 201:**
```json
{
  "id": "uuid",
  "voucher_type": "Receipt",
  "voucher_number": null,
  "date": "2026-05-08",
  "narration": "Payment received from Sharma Traders",
  "reference": "UTR1234567",
  "total_amount": "50000.00",
  "status": "posted",
  "source": "manual",
  "is_auto_posted": false,
  "confidence_score": null,
  "gst_applicable": false,
  "cgst": "0.00",
  "sgst": "0.00",
  "igst": "0.00",
  "cess": "0.00",
  "tds_applicable": false,
  "tds_amount": "0.00",
  "tally_posted_at": null,
  "created_by": "uuid",
  "created_at": "...",
  "entries": [
    { "id": "uuid", "ledger_id": "...", "amount": "50000.00", "entry_type": "Dr", "line_number": 1 },
    { "id": "uuid", "ledger_id": "...", "amount": "50000.00", "entry_type": "Cr", "line_number": 2 }
  ]
}
```

**Validation rules:**
- `entries` length ≥ 2.
- `total_amount` equals the sum of `Dr` entries (which must equal sum of `Cr` entries).
- All `ledger_id`s belong to the active company.
- `voucher_type='Sales'`: at least one entry is on a "Sundry Debtors" group ledger; `Purchase`: "Sundry Creditors"; `Contra`: only Bank/Cash group ledgers; etc. Specific business rules per type.
- If `gst_applicable=true`, `place_of_supply` is required.
- `cgst + sgst + igst + cess` may be zero if `gst_applicable=true` and the supply is exempt; otherwise must be > 0.

**Errors:**
- `400 idempotency_key_required`
- `409 idempotency_replay` — idempotency key matched a prior request; same response returned (per IDEMPOTENCY.md)
- `409 voucher_number_collision` — explicit voucher_number conflicts
- `422 validation_error` — entries don't balance, ledger doesn't belong, etc.

**Side effects:**
- An audit log row with `action='voucher.created'` is written in the same transaction.
- If the company has an active Tally connector, a `post_voucher_to_tally` Celery task is enqueued.

#### `GET /api/v1/vouchers/`

List vouchers.

**Query parameters:**
- `voucher_type?: string`
- `from?: date`, `to?: date` — date range filter
- `status?: string`
- `ledger_id?: uuid` — vouchers involving this ledger
- `source?: string`
- `limit?: int`, `cursor?: string`

**Response 200:** paginated list of voucher objects.

#### `GET /api/v1/vouchers/{voucher_id}`

Get a voucher with its entries.

**Response 200:** voucher object including `entries[]`.

#### `PATCH /api/v1/vouchers/{voucher_id}`

Update mutable fields. Limited mutations: `narration`, `reference` only. Voucher amounts and entries are immutable post-creation; to correct an error, cancel and re-create.

**Response 200:** updated voucher.

**Errors:**
- `409 voucher_immutable_field` — attempt to modify immutable field
- `409 voucher_already_cancelled`

#### `POST /api/v1/vouchers/{voucher_id}/cancel`

Cancel a voucher (sets status='cancelled'). The voucher remains in the database; this is the only way to "undo" a voucher.

**Request:**
```json
{ "reason": "Duplicate entry" }
```

**Response 200:** updated voucher with `status='cancelled'`.

**Side effects:** audit row `action='voucher.cancelled'`. If voucher was posted to Tally, a reversal voucher is **not** auto-created — that is a future feature.

---

### Connector Status & Health

#### `GET /api/v1/connector/status`

Returns whether the active company's Tally connector is currently connected.

Requires auth and `X-Company-ID`.

**Response 200:**
```json
{
  "company_id": "uuid",
  "connected": true,
  "last_seen_at": "2026-05-08T10:00:00+05:30",
  "tally_running": true,
  "tally_version": "3.0",
  "connector_version": "1.0.0",
  "queued_outbound_count": 0
}
```

If the connector is not connected:
```json
{
  "company_id": "uuid",
  "connected": false,
  "last_seen_at": "2026-05-07T22:00:00+05:30"
}
```

#### `POST /api/v1/connector/sync/{company_id}`

Trigger a master-data sync from Tally. Requires auth, `X-Company-ID`, and Idempotency-Key.

**Request:** empty body.

**Response 202:**
```json
{
  "task_id": "uuid",
  "status": "sync_triggered",
  "estimated_duration_seconds": 30
}
```

**Errors:**
- `503 connector_offline` — connector not connected
- `400 idempotency_key_required`

#### `WS /api/v1/connector/ws`

WebSocket endpoint that the Tally Connector connects to. Not called by mobile/web clients. See `CONNECTOR_PROTOCOL.md`.

Authentication: connector token via `Authorization: Bearer <connector-token>` on the WebSocket upgrade request, plus `X-Company-ID` header.

---

### Health

#### `GET /api/v1/health`

Liveness probe. Does not require auth.

**Response 200:**
```json
{ "status": "healthy", "version": "0.1.0" }
```

#### `GET /api/v1/health/ready`

Readiness probe — checks DB and Redis. Does not require auth.

**Response 200:**
```json
{
  "status": "ready",
  "database": "ok",
  "redis": "ok"
}
```

**Response 503** if any dependency is down.

---

## Phase 1 Endpoints (Invoice Scan — the wedge)

### Ingestions

#### `POST /api/v1/ingestions/`

Submit a capture (image, PDF, message text) for processing. Requires auth, `X-Company-ID`, **Idempotency-Key required**.

**Request:** multipart/form-data:
- `source`: one of `photo`, `pdf`, `whatsapp`, `email`, `csv`, `voice`, `manual`
- `file`: file upload (for binary sources)
- `text`: text content (for non-binary sources like whatsapp/sms)
- `sender_identifier?`: optional source identifier (phone, email)

**Response 202:**
```json
{
  "id": "uuid",
  "company_id": "uuid",
  "source": "photo",
  "status": "received",
  "received_at": "2026-05-08T10:00:00+05:30"
}
```

**Side effects:** an `extract_invoice` (or other source-appropriate) Celery task is enqueued.

**Errors:**
- `400 idempotency_key_required`
- `409 idempotency_replay`
- `413 file_too_large` — file exceeds 10MB
- `415 unsupported_media_type`
- `422 validation_error`

#### `GET /api/v1/ingestions/{ingestion_id}`

Get the status of an ingestion.

**Response 200:**
```json
{
  "id": "uuid",
  "source": "photo",
  "status": "extracted",
  "received_at": "...",
  "extracted_at": "...",
  "draft_voucher_id": "uuid",
  "failure_reason": null
}
```

#### `GET /api/v1/ingestions/`

List ingestions.

**Query parameters:** `status?`, `source?`, `from?`, `to?`, `limit?`, `cursor?`.

**Response 200:** paginated list.

---

### Optional Vouchers (Review Queue) — v1.2

In v1.2, AI-extracted ingestions land directly in the `vouchers` table with `is_optional_in_tally=true`. There is no separate `draft_vouchers` table. The review queue is a filter on `vouchers`.

#### `GET /api/v1/vouchers/?is_optional=true&review_status=pending`

List vouchers awaiting admin approval (Optional in Tally, not yet approved or rejected).

**Query parameters:**
- `is_optional?: boolean` — filter to Optional vouchers
- `review_status?: string` — `pending` (default for is_optional=true), `approved`, `rejected`
- `min_confidence?: number`, `max_confidence?: number`
- `source?: string` — `photo`, `pdf`, `sms`, etc.
- `limit?`, `cursor?`

**Response 200:** paginated list of voucher objects with extra fields:
```json
{
  "items": [
    {
      "id": "uuid",
      "voucher_type": "Purchase",
      "date": "2026-05-08",
      "total_amount": "11800.00",
      "is_optional_in_tally": true,
      "approved_to_regular_at": null,
      "confidence_score": 0.92,
      "source": "photo",
      "source_ingestion_id": "uuid",
      "tally_posted_at": "2026-05-08T10:05:00+05:30",
      "tally_voucher_guid": "...",
      "extraction_flags": ["new_party"],
      "entries": [...]
    }
  ],
  "meta": { "next_cursor": "...", "total": 12 }
}
```

#### `POST /api/v1/vouchers/{voucher_id}/approve-to-regular`

Promote an Optional voucher in Tally to Regular. **Idempotency-Key required.**

**Request:** empty or:
```json
{ "notes": "Verified against physical bill" }
```

**Response 200:**
```json
{
  "id": "uuid",
  "is_optional_in_tally": false,
  "approved_to_regular_at": "2026-05-08T11:00:00+05:30",
  "approved_to_regular_by": "uuid",
  "status": "posted"
}
```

**Side effects:**
- Connector command `approve_optional_voucher` issued
- On success: `is_optional_in_tally=false`, `approved_to_regular_at` set, `approved_to_regular_by` set, `status='posted'`
- Audit log: `voucher.approved_to_regular`
- Push notification to user: "Voucher approved"

**Errors:**
- `409 voucher_not_optional` — already Regular
- `409 voucher_rejected` — already rejected
- `503 connector_offline` — must wait for connector

#### `POST /api/v1/vouchers/{voucher_id}/reject-optional`

Reject an Optional voucher; the connector deletes it from Tally entirely.

**Request:**
```json
{ "reason": "Personal expense, not business" }
```

**Response 200:**
```json
{
  "id": "uuid",
  "status": "rejected_optional",
  "optional_rejection_reason": "Personal expense, not business",
  "optional_rejected_at": "2026-05-08T11:00:00+05:30"
}
```

**Side effects:**
- Connector command `reject_optional_voucher` issued
- Voucher status set to `rejected_optional`
- Audit log: `voucher.rejected_optional`

**Errors:**
- `409 voucher_not_optional` — already Regular; cannot silently delete
- `503 connector_offline`

---

## Audit Logs (P0 read API)

#### `GET /api/v1/audit-logs/`

List audit logs for the active company. Requires `owner` or `admin` role.

**Query parameters:**
- `entity_type?`, `entity_id?`, `user_id?`, `action?`, `from?`, `to?`, `limit?`, `cursor?`

**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "user_email": "user@example.com",
      "action": "voucher.created",
      "entity_type": "voucher",
      "entity_id": "uuid",
      "old_value": null,
      "new_value": { "..." },
      "changes": {},
      "ip_address": "203.0.113.42",
      "request_id": "uuid",
      "source": "api",
      "created_at": "..."
    }
  ],
  "meta": { "next_cursor": "...", "total": 1543 }
}
```

**Errors:**
- `403 insufficient_role`

---

## Reports (v1.2 — Phase 0)

All report endpoints require auth, `X-Company-ID`, role ≥ `viewer`. Computation rules in `REPORTS.md`.

### `GET /api/v1/reports/trial-balance`

Query: `as_of_date` (default today).

Response shape: see `REPORTS.md` § Trial Balance.

### `GET /api/v1/reports/profit-loss`

Query: `from_date`, `to_date` (default current FY).

Response shape: see `REPORTS.md` § Profit & Loss.

### `GET /api/v1/reports/balance-sheet`

Query: `as_of_date` (default today).

Response shape: see `REPORTS.md` § Balance Sheet.

### `GET /api/v1/reports/outstanding`

Query: `type` (`receivables` | `payables`, required), `as_of_date` (default today).

Response shape: see `REPORTS.md` § Outstanding.

---

## Analytics (v1.2 — Phase 1)

All analytics endpoints require auth, `X-Company-ID`, role ≥ `viewer`. Computation rules in `REPORTS.md`.

### `GET /api/v1/analytics/gst-liability`

Query: `from_date`, `to_date` (default current month).

Response: see `REPORTS.md` § GST Liability Summary. The response includes a `disclaimer` field reminding users this is indicative, not filing-grade.

### `GET /api/v1/analytics/aged-outstanding`

Query: `type` (required), `as_of_date` (default today).

Response: see `REPORTS.md` § Aged Outstanding.

### `GET /api/v1/analytics/top-debtors`

Query: `as_of_date`, `limit` (default 5, max 20).

Response shape:
```json
{
  "items": [
    { "ledger_id": "uuid", "ledger_name": "Sharma Traders", "balance": "1500000.00" }
  ]
}
```

### `GET /api/v1/analytics/top-creditors`

Same shape as `top-debtors`, for Sundry Creditors.

### `GET /api/v1/analytics/top-expenses`

Query: `from_date`, `to_date`, `limit`.

Response shape: top expense ledgers by P&L movement.

### `GET /api/v1/analytics/cash-flow`

Query: `months` (default 6, max 24).

Response: see `REPORTS.md` § Cash Flow.

### `GET /api/v1/analytics/tds-payable`

Query: `from_date`, `to_date`.

Response: see `REPORTS.md` § TDS Payable Summary.

---

## Dashboard (v1.2 — Phase 0)

### `GET /api/v1/dashboard/home`

Single endpoint that hydrates the mobile home screen. Performance budget: 500ms p95.

Response: see `REPORTS.md` § Dashboard.

---

## Account & Data Lifecycle (v1.2)

### `POST /api/v1/account/deletion-request` (Phase 0)

Request account deletion. Starts a 30-day grace period.

**Request:** empty or:
```json
{ "reason": "no longer needed" }
```

**Response 201:**
```json
{
  "id": "uuid",
  "status": "grace_period",
  "requested_at": "2026-05-08T10:00:00+05:30",
  "grace_ends_at": "2026-06-07T10:00:00+05:30"
}
```

**Errors:**
- `409 ownership_transfer_required` — user is sole owner of one or more companies; must transfer or delete those first. Response includes `details.companies`.

**Side effects:** audit log `account.deletion_requested`. Email confirming the request and the cancellation deadline.

### `DELETE /api/v1/account/deletion-request` (Phase 0)

Cancel a pending deletion request during the grace period.

**Response 200:**
```json
{ "status": "cancelled", "cancelled_at": "2026-05-09T10:00:00+05:30" }
```

**Side effects:** audit log `account.deletion_cancelled`.

### `POST /api/v1/account/data-export` (Phase 1)

Request a complete data export. Scoped to a company or to all the user's companies.

**Request:**
```json
{ "company_id": "uuid-or-null" }
```

If `company_id` is null, exports data for all companies the user belongs to where they have role ≥ `admin`.

**Response 202:**
```json
{
  "id": "uuid",
  "status": "pending",
  "estimated_minutes": 5
}
```

**Side effects:** audit log `data_export.requested`. Celery task generates ZIP, emails signed S3 URL on completion (7-day expiry).

### `GET /api/v1/account/data-export/{request_id}` (Phase 1)

Status of a data export request.

**Response 200:**
```json
{
  "id": "uuid",
  "status": "completed",
  "download_url": "https://...",
  "download_url_expires_at": "2026-05-15T10:00:00+05:30",
  "completed_at": "2026-05-08T10:05:00+05:30"
}
```

---

## Devices & Push Notifications (v1.2 — Phase 0/1)

### `POST /api/v1/devices/register`

Register a device for push notifications.

**Request:**
```json
{
  "token": "fcm-or-apns-token",
  "platform": "android",
  "app_version": "1.0.0"
}
```

**Response 201:**
```json
{ "id": "uuid", "token_registered": true }
```

**Side effects:** audit log `device.registered`.

### `DELETE /api/v1/devices/{device_id}`

Unregister a device (e.g., on logout).

**Response 204.**

**Side effects:** audit log `device.unregistered`.

---

## Onboarding (v1.2 — Phase 0)

### `GET /api/v1/onboarding/checklist`

Returns the onboarding state for the active company.

**Response 200:**
```json
{
  "company_id": "uuid",
  "items": [
    { "key": "company_created", "label": "Create your company", "completed": true, "completed_at": "..." },
    { "key": "connector_installed", "label": "Install Tally Connector", "completed": false },
    { "key": "ledgers_synced", "label": "Sync ledgers from Tally", "completed": false },
    { "key": "first_voucher_posted", "label": "Post your first voucher", "completed": false },
    { "key": "first_invoice_extracted", "label": "Try invoice scan (Phase 1+)", "completed": false }
  ],
  "completed_count": 1,
  "total_count": 5
}
```

The list is computed from existing data — no separate "checklist" table. Each item maps to a query against the relevant table.

---

## Admin Cost Tracking (v1.2 — Phase 1, internal)

### `GET /api/v1/admin/cost-tracking`

Restricted to `is_superuser=true` users.

Query: `company_id?`, `month?` (default current month).

Response: see `REPORTS.md` § Cost Tracking.

---



The following `error.code` values are stable v1 contracts. Clients depend on them.

| Code | HTTP | Meaning |
|---|---|---|
| `validation_error` | 422 | Request body failed Pydantic validation |
| `invalid_credentials` | 401 | Wrong email/password or invalid token |
| `user_inactive` | 403 | User account deactivated |
| `insufficient_role` | 403 | User lacks required role on company |
| `email_already_registered` | 409 | Email exists during registration |
| `gstin_already_registered` | 409 | GSTIN exists during company creation |
| `company_not_found` | 404 | Company doesn't exist OR no access |
| `voucher_not_found` | 404 | Voucher doesn't exist OR no access |
| `ledger_not_found` | 404 | Ledger doesn't exist OR no access |
| `voucher_immutable_field` | 409 | Attempt to modify post-creation immutable field |
| `voucher_already_cancelled` | 409 | Cannot cancel an already-cancelled voucher |
| `voucher_number_collision` | 409 | Explicit voucher_number conflicts |
| `voucher_entries_unbalanced` | 422 | Dr total != Cr total |
| `ledger_in_use` | 409 | Cannot deactivate ledger with entries |
| `idempotency_key_required` | 400 | Missing required Idempotency-Key header |
| `idempotency_replay` | 409 | Replayed key with mismatched body |
| `connector_offline` | 503 | Tally connector not connected |
| `file_too_large` | 413 | Upload exceeds size limit |
| `unsupported_media_type` | 415 | File type not supported |
| `draft_already_posted` | 409 | (deprecated in v1.2) |
| `draft_rejected` | 409 | (deprecated in v1.2) |
| `voucher_not_optional` | 409 | (v1.2) Approve/reject called on a non-Optional voucher |
| `voucher_rejected` | 409 | (v1.2) Voucher already rejected |
| `ownership_transfer_required` | 409 | (v1.2) Account deletion blocked; user is sole owner |
| `extraction_quota_exceeded` | 429 | (v1.2) Daily AI extraction limit reached |
| `rate_limit_exceeded` | 429 | Too many requests |

New error codes added in later phases extend this table; existing codes are not renamed.

---

## Rate limiting (Phase 0)

Per-user limits enforced by Redis sliding window:

- `/api/v1/auth/login`: 5 requests per minute per IP, 20 per hour per email
- `/api/v1/auth/register`: 3 requests per hour per IP
- All other authenticated endpoints: 600 requests per minute per user

Rate limit responses include:
```
Retry-After: <seconds>
X-RateLimit-Limit: <limit>
X-RateLimit-Remaining: <count>
```

Exceeding the limit returns `429 rate_limit_exceeded`.

---

## OpenAPI generation

The API is documented via FastAPI's auto-generated OpenAPI spec at `/api/v1/openapi.json`. Coder Claude verifies the spec matches this document on every PR via a contract test (`tests/integration/test_openapi_contract.py`). Drift fails CI.
