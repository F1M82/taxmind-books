# Tally Connector Protocol

**Status:** Frozen for v1. Protocol versioning rules below allow extension; breaking changes require a major version bump.

This document defines the wire protocol between the cloud backend and the Tally Desktop Connector. It is the contract both sides implement against. The connector salvaged from `Qwen-Tallyonmobile` is being rebuilt to this protocol; the legacy code does not match this spec.

## Architecture recap

```
       (cloud)                                    (MSME's PC)
   ┌──────────────┐    WSS over Internet     ┌──────────────────┐
   │              │  ◄─────────────────────► │                  │
   │   Backend    │                          │ Tally Connector  │
   │   FastAPI    │   outbound WS only       │   (Python exe)   │
   │              │   from connector         │                  │
   └──────────────┘                          └────────┬─────────┘
                                                      │ HTTP/XML
                                                      │ localhost:9000
                                                      ▼
                                             ┌──────────────────┐
                                             │   TallyPrime     │
                                             │   (3.0+)         │
                                             └──────────────────┘
```

**Connection direction:** the connector initiates. The backend never reaches into the customer's network. This is a hard requirement — MSME firewalls do not permit inbound connections, and forcing them to is a non-starter for adoption.

**Transport:** WebSocket over TLS (`wss://`). Heartbeat-driven. Auto-reconnect on disconnect with exponential backoff (capped at 60 seconds).

**Authentication:** JWT-style connector token, separate from user JWTs. Issued at connector enrollment. Bound to one company. Rotatable, revocable.

## Connector token

The connector token is a JWT signed by the backend with a dedicated secret (`CONNECTOR_JWT_SECRET`, distinct from `JWT_SECRET`). Payload:

```json
{
  "sub": "connector-uuid",
  "company_id": "company-uuid",
  "kind": "connector",
  "iat": 1714834800,
  "exp": 1746370800
}
```

Tokens expire after 1 year. Rotation is admin-initiated (Phase 5+ feature; v1 issues a long-lived token at enrollment). Revocation is immediate via a `revoked_connectors` table (Phase 1+).

The token is provisioned during connector installation:
1. User logs into mobile/web app.
2. Settings → "Install Tally Connector" → backend issues a one-time enrollment code (15-minute TTL).
3. User runs the connector installer on Windows; the installer prompts for the enrollment code.
4. Connector exchanges the code for a connector token via `POST /api/v1/connector/enroll` (one-time use).
5. Token stored in Windows Credential Manager (or `%APPDATA%\TaxMindBooks\token.dat` encrypted with DPAPI).

The enrollment endpoint is documented in `API.md` (Phase 1 addition).

## WebSocket lifecycle

### Connection

```
Connector  →  Backend
GET /api/v1/connector/ws HTTP/1.1
Host: api.taxmindbooks.in
Upgrade: websocket
Connection: Upgrade
Authorization: Bearer <connector-token>
X-Company-ID: <company-uuid>
X-Connector-Version: 1.0.0
X-Protocol-Version: 1
```

The backend validates:
1. Token signature and `kind === "connector"`.
2. Token's `company_id` matches the `X-Company-ID` header. Mismatch → close with `4003`.
3. Token not in `revoked_connectors`. Revoked → close with `4001`.
4. `X-Protocol-Version` is supported. Unsupported → close with `4400`.

On success, the backend accepts the upgrade and registers the connection in `active_connectors[company_id]`.

### Close codes

| Code | Meaning | Connector behavior |
|---|---|---|
| 1000 | Normal closure | Reconnect after baseline delay |
| 1001 | Going away (server shutdown) | Reconnect with backoff |
| 1006 | Abnormal closure (network drop) | Reconnect with backoff |
| 4001 | Token revoked | Stop. Display "connector disabled" to user. |
| 4002 | Token expired | Refresh token via REST; reconnect. |
| 4003 | Company mismatch | Stop. Bug; log and report. |
| 4400 | Protocol version unsupported | Stop. Prompt user to upgrade connector. |
| 4429 | Too many connections | Backoff aggressively. |

### Reconnect backoff

Initial delay 1 second. Doubles on each consecutive failure: 1, 2, 4, 8, 16, 32, 60 (capped). Reset on successful registration.

Jitter: ±20% randomization on each delay to avoid thundering herd when many connectors reconnect simultaneously after an outage.

### Heartbeat

The connector sends a `heartbeat` message every 30 seconds. The backend responds with `heartbeat_ack`. If the connector receives no ack for 60 seconds, it treats the connection as dead and reconnects.

The backend tracks `last_heartbeat_at` per connection. If no heartbeat for 90 seconds, the connection is removed from the active registry (the WS object may still be technically open but considered stale).

## Message envelope

Every message is JSON. Every message has a stable envelope:

```json
{
  "type": "<message_type>",
  "request_id": "<uuid-v4>",
  "ts": "<iso8601-utc>",
  "payload": { ... }
}
```

Fields:
- `type`: one of the enumerated message types below. Strings are stable; case-sensitive.
- `request_id`: UUID v4 generated by the sender. Used for correlating request/response pairs and for idempotency.
- `ts`: sender's ISO-8601 timestamp in UTC. Informational; backend uses its own clock for authoritative timestamps.
- `payload`: type-specific body. Schema per type below.

Messages without these fields are rejected with a `protocol_error`. The connection is **not** closed for protocol errors during normal operation — the offending message is dropped and an error reply is sent.

Maximum message size: 1 MB. Larger payloads (e.g., bulk ledger sync) are paginated.

## Message catalog

The catalog is grouped by direction.

### Connector → Backend

#### `register`

Sent immediately after WebSocket open. Identifies the connector and reports the local Tally state.

```json
{
  "type": "register",
  "request_id": "...",
  "ts": "2026-05-08T10:00:00Z",
  "payload": {
    "connector_version": "1.0.0",
    "protocol_version": 1,
    "tally_running": true,
    "tally_version": "3.0",
    "tally_company_open": "Acme Traders",
    "host": {
      "os": "Windows 11",
      "hostname": "DESKTOP-ABC123",
      "user": "gaurav"
    },
    "queued_outbound_count": 0
  }
}
```

The backend responds with a `register_ack` (see below). If the registration is rejected (token problem, etc.), the backend sends an `error` and closes the WS with the appropriate close code.

#### `heartbeat`

Every 30 seconds. Empty payload acceptable; optional fields update connector state.

```json
{
  "type": "heartbeat",
  "request_id": "...",
  "ts": "...",
  "payload": {
    "tally_running": true,
    "queued_outbound_count": 3
  }
}
```

#### `command_result`

Reply to a backend-issued command. The `request_id` matches the original command's `request_id`.

```json
{
  "type": "command_result",
  "request_id": "<command's request_id>",
  "ts": "...",
  "payload": {
    "command": "post_voucher",
    "status": "success",
    "result": {
      "tally_voucher_guid": "...",
      "tally_voucher_number": "RCT-001"
    },
    "duration_ms": 380
  }
}
```

On failure:

```json
{
  "type": "command_result",
  "request_id": "...",
  "ts": "...",
  "payload": {
    "command": "post_voucher",
    "status": "error",
    "error": {
      "code": "tally_validation_failed",
      "message": "Ledger 'Sharma Traders' not found in Tally",
      "details": { "tally_response": "..." }
    },
    "duration_ms": 120,
    "retryable": false
  }
}
```

`status` is `success` | `error` | `partial`. `partial` is used by bulk operations (e.g., bulk ledger sync where some succeeded and some failed); `result` then includes per-item status.

`retryable` informs the backend whether to retry. Network/timeout errors are retryable; validation errors are not.

#### `tally_event`

Connector-initiated event when Tally state changes locally. v1 uses this only for `tally_company_changed` (the user opened a different company in Tally). Backend reacts by halting outbound commands until the connector reports the expected company is reopened.

```json
{
  "type": "tally_event",
  "request_id": "...",
  "ts": "...",
  "payload": {
    "event": "tally_company_changed",
    "from": "Acme Traders",
    "to": "Beta Industries"
  }
}
```

#### `error`

Connector-side error reporting. Used for unexpected errors the connector cannot tie to a specific command.

```json
{
  "type": "error",
  "request_id": "...",
  "ts": "...",
  "payload": {
    "code": "tally_unreachable",
    "message": "Tally HTTP server not responding on port 9000.",
    "context": {
      "last_successful_ping": "2026-05-08T09:55:00Z"
    }
  }
}
```

The backend logs these and may surface them in the `GET /api/v1/connector/status` response.

### Backend → Connector

#### `register_ack`

Reply to `register`. Confirms enrollment; may include initial commands.

```json
{
  "type": "register_ack",
  "request_id": "<register's request_id>",
  "ts": "...",
  "payload": {
    "connector_id": "uuid",
    "company_id": "uuid",
    "company_name": "Acme Traders",
    "expected_tally_company": "Acme Traders",
    "server_version": "0.1.0",
    "protocol_version": 1,
    "next_actions": [
      { "action": "sync_masters", "request_id": "uuid" }
    ]
  }
}
```

`next_actions` is optional — used to immediately ask the connector to sync ledgers/groups on first connection or after a long disconnect.

#### `heartbeat_ack`

Reply to `heartbeat`. Empty payload.

```json
{
  "type": "heartbeat_ack",
  "request_id": "<heartbeat's request_id>",
  "ts": "...",
  "payload": {}
}
```

#### `command`

Backend issues a command for the connector to execute. The `request_id` is what the connector echoes in `command_result`.

```json
{
  "type": "command",
  "request_id": "uuid",
  "ts": "...",
  "payload": {
    "company_id": "<uuid>",
    "command": "<command_name>",
    "args": { ... },
    "timeout_seconds": 30,
    "idempotency_key": "<uuid>"
  }
}
```

`company_id` is included so the connector can verify the command targets the company it registered for. Mismatch → connector replies with `command_result` status=error, code=`company_mismatch`, and logs locally.

`idempotency_key` is the connector-side dedup key. The connector maintains a local SQLite cache of `(command, idempotency_key) → result` for 24 hours. A repeat command with the same key returns the cached result without re-executing against Tally.

`timeout_seconds` is how long the connector waits for Tally to respond. Default 30. Bulk operations use higher values.

#### `error`

Backend-side error. Reasons: invalid message, expired token (but not expired enough to close), rate-limited.

```json
{
  "type": "error",
  "request_id": "<sender's request_id, if applicable>",
  "ts": "...",
  "payload": {
    "code": "rate_limited",
    "message": "Connector exceeded heartbeat rate.",
    "retry_after_seconds": 5
  }
}
```

## Command catalog

Commands the backend issues to the connector. Each command's `args` and `result` schemas are below.

### `ping`

Sanity check. Connector pings Tally and reports.

**args:** `{}`
**result:**
```json
{
  "tally_responsive": true,
  "tally_version": "3.0",
  "ping_duration_ms": 12
}
```

### `sync_masters`

Pull all ledgers and groups from Tally. Used at first connection and on demand.

**args:**
```json
{ "since": "2026-05-01T00:00:00Z" }
```

`since` is optional. If absent, full sync. If present, incremental — connector returns only ledgers modified since.

(Note: Tally's XML API doesn't expose modified-since natively for ledgers. v1 implements this as a full pull on the connector side, with the connector hashing ledger payloads and returning only changed ones. Future improvement.)

**result (chunked via partial status):**
```json
{
  "ledgers": [
    {
      "tally_master_id": "12345",
      "name": "Sharma Traders",
      "group_name": "Sundry Debtors",
      "parent_ledger_name": null,
      "opening_balance": "0.00",
      "balance_type": "Dr",
      "gstin": "27BBBBB5678B1Z5",
      "pan": "BBBBB5678B",
      "phone": "9876543210",
      "email": "sharma@example.com",
      "address": "...",
      "state_code": "27"
    }
  ],
  "groups": [
    {
      "name": "Sundry Debtors",
      "parent": "Current Assets",
      "nature": "Assets"
    }
  ],
  "totals": { "ledger_count": 142, "group_count": 28 }
}
```

For large companies (>1000 ledgers), the connector splits the response across multiple `command_result` messages with `status: "partial"` for intermediate batches and `status: "success"` for the final batch. Each partial includes a `batch_index` and `total_batches` field.

### `post_voucher`

Post a voucher to Tally.

**args:**
```json
{
  "voucher_id": "<uuid>",
  "voucher_type": "Receipt",
  "voucher_number": null,
  "date": "2026-05-08",
  "narration": "Payment received from Sharma Traders",
  "reference": "UTR1234567",
  "as_optional": false,
  "entries": [
    {
      "ledger_name": "Bank Account",
      "amount": "50000.00",
      "entry_type": "Dr"
    },
    {
      "ledger_name": "Sharma Traders",
      "amount": "50000.00",
      "entry_type": "Cr"
    }
  ],
  "gst": null,
  "tds": null
}
```

**v1.2:** the `as_optional` field controls whether the connector marks the voucher as Optional in TallyPrime (translates to `<ISOPTIONAL>Yes</ISOPTIONAL>` in the XML envelope). Optional vouchers exist in Tally but do not affect financial statements until promoted to Regular via `approve_optional_voucher`. Manual mobile entries set `as_optional=false`; AI-extracted entries set `as_optional=true`.

**result:**
```json
{
  "tally_voucher_guid": "...",
  "tally_voucher_number": "RCT-001",
  "tally_xml_response_excerpt": "..."
}
```

The connector translates this into Tally's XML envelope (using the salvaged `tally_client.py`), submits via HTTP/XML, parses Tally's response, and reports back. `voucher_number` may be null in args if Tally is configured to auto-number; the result includes the number Tally assigned.

The post is idempotent on `idempotency_key`. If the connector's local cache shows the same key was previously posted with success, it returns the cached result without re-posting. This handles the case where the backend retries the WS command after a network drop, but Tally already accepted the post.

**Errors:**
- `tally_unreachable` — Tally HTTP server not responding (retryable)
- `tally_validation_failed` — Tally rejected the XML (not retryable; see `error.details.tally_response`)
- `tally_company_mismatch` — the open company in Tally doesn't match `expected_tally_company` (retryable after user fixes)
- `ledger_not_found` — a ledger named in the entries doesn't exist in Tally (retryable after sync_masters)
- `voucher_number_collision` — explicit voucher_number conflicts in Tally (not retryable)
- `tally_timeout` — Tally took longer than `timeout_seconds` (retryable)

### `cancel_voucher`

Cancel a voucher in Tally. Tally doesn't have a "cancel" primitive in the same sense; the connector implements this by either (a) deleting the voucher if Tally permits and our policy allows, or (b) creating a reversing entry. v1 implements option (a) — direct delete — and audits both sides.

**args:**
```json
{
  "voucher_id": "<our uuid>",
  "tally_voucher_guid": "<tally's guid>",
  "reason": "Cancelled by user"
}
```

**result:**
```json
{ "deleted": true, "tally_response_excerpt": "..." }
```

(Phase 1+ feature. Mentioned here for completeness; not part of Phase 0 implementation.)

### `approve_optional_voucher` (v1.2)

Promote an Optional voucher in Tally to Regular. After approval, the voucher contributes to financial statements.

**args:**
```json
{
  "voucher_id": "<our uuid>",
  "tally_voucher_guid": "<tally's guid>"
}
```

**result:**
```json
{
  "promoted": true,
  "tally_response_excerpt": "..."
}
```

The connector implements this by sending an XML alteration request to TallyPrime that sets `<ISOPTIONAL>No</ISOPTIONAL>` on the existing voucher, identified by GUID. Tally re-evaluates the voucher and includes it in financial reports from that point.

**Errors:**
- `tally_voucher_not_found` — the voucher_guid no longer exists in Tally (user may have deleted it directly)
- `voucher_already_regular` — the voucher is already non-Optional (idempotent: returns success)
- `tally_unreachable` (retryable)

### `reject_optional_voucher` (v1.2)

Delete an Optional voucher from Tally entirely. Used when admin rejects an AI-extracted draft.

**args:**
```json
{
  "voucher_id": "<our uuid>",
  "tally_voucher_guid": "<tally's guid>",
  "reason": "Personal expense, not business"
}
```

**result:**
```json
{
  "deleted": true,
  "tally_response_excerpt": "..."
}
```

The connector sends an XML deletion request for the voucher. Tally removes it; no financial trace remains.

**Errors:**
- `tally_voucher_not_found` — already gone (idempotent: returns success with `deleted=true`)
- `voucher_not_optional` — the voucher has been promoted to Regular and cannot be silently deleted; admin must use `cancel_voucher` instead
- `tally_unreachable` (retryable)

### `get_outstanding`

Fetch outstanding receivables/payables from Tally for a given period.

**args:**
```json
{
  "party_type": "Sundry Debtors",
  "as_of_date": "2026-05-08"
}
```

**result:**
```json
{
  "as_of_date": "2026-05-08",
  "items": [
    {
      "ledger_name": "Sharma Traders",
      "ledger_gstin": "27BBBBB5678B1Z5",
      "balance": "125000.00",
      "balance_type": "Dr",
      "bills": [
        { "bill_ref": "INV-2026-145", "date": "2026-04-15", "amount": "50000.00" },
        { "bill_ref": "INV-2026-152", "date": "2026-04-22", "amount": "75000.00" }
      ]
    }
  ],
  "total": "125000.00"
}
```

(Phase 3+ feature, used by reconciliation.)

### `get_ledger_history`

Fetch transaction history for a ledger over a date range. Used by reconciliation to obtain "your transactions" for matching.

**args:**
```json
{
  "ledger_name": "Sharma Traders",
  "from_date": "2026-04-01",
  "to_date": "2026-05-08"
}
```

**result:**
```json
{
  "ledger_name": "Sharma Traders",
  "opening_balance": "0.00",
  "transactions": [
    {
      "date": "2026-04-05",
      "voucher_type": "Sales",
      "voucher_number": "INV-2026-145",
      "narration": "Sales to Sharma Traders",
      "reference": "INV-2026-145",
      "amount": "50000.00",
      "entry_type": "Dr",
      "tally_guid": "..."
    }
  ],
  "closing_balance": "125000.00"
}
```

(Phase 3+ feature.)

## Backend-side connection registry

The backend maintains an in-memory registry of active connections, keyed by `company_id`:

```python
# backend/app/services/tally/connector_registry.py
from typing import Dict
from fastapi import WebSocket

active_connectors: Dict[str, "ConnectorConnection"] = {}


class ConnectorConnection:
    def __init__(self, ws: WebSocket, company_id: str, connector_id: str):
        self.ws = ws
        self.company_id = company_id
        self.connector_id = connector_id
        self.last_heartbeat_at: datetime = datetime.utcnow()
        self.tally_running: bool = False
        self.tally_version: str | None = None
        self.pending_commands: dict[str, asyncio.Future] = {}

    async def send_command(self, command: str, args: dict, timeout: int = 30,
                           idempotency_key: str | None = None) -> dict:
        request_id = str(uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending_commands[request_id] = future
        message = {
            "type": "command",
            "request_id": request_id,
            "ts": datetime.utcnow().isoformat() + "Z",
            "payload": {
                "company_id": self.company_id,
                "command": command,
                "args": args,
                "timeout_seconds": timeout,
                "idempotency_key": idempotency_key or str(uuid4()),
            },
        }
        await self.ws.send_json(message)
        try:
            return await asyncio.wait_for(future, timeout=timeout + 5)
        finally:
            self.pending_commands.pop(request_id, None)

    async def handle_command_result(self, request_id: str, result: dict) -> None:
        future = self.pending_commands.get(request_id)
        if future and not future.done():
            future.set_result(result)
```

**Multi-instance caveat:** the in-memory registry doesn't work across multiple backend pods. For v1 we run a single backend pod (Railway's default). Phase 5+ adds Redis pub/sub to fan out commands when scaling horizontally.

## Voucher dispatch flow

When a voucher is posted via `POST /api/v1/vouchers/`:

1. The voucher is created in the DB inside the request transaction.
2. An audit row `voucher.created` is written (same transaction).
3. The transaction commits.
4. A Celery task `post_voucher_to_tally(voucher_id, company_id, user_id, request_id)` is enqueued.
5. The worker:
   a. Loads the voucher from DB.
   b. Looks up the active connector connection for the company.
   c. If absent: marks `voucher.tally_post_attempts += 1`, writes audit `voucher.tally_post_failed` with `code=connector_offline`, schedules retry via Celery's exponential backoff.
   d. If present: sends `command` over WS, awaits result.
   e. On success: updates `voucher.tally_posted_at`, `voucher.tally_voucher_guid`, writes audit `voucher.posted_to_tally`.
   f. On retryable error: schedules retry.
   g. On non-retryable error: writes audit `voucher.tally_post_failed` with the error details. Voucher stays in DB; user can view in Tally Sync queue UI and decide what to do.

The Celery retry policy: 5 attempts with exponential backoff (1, 2, 4, 8, 16 minutes). Past 5 attempts, the voucher enters the "manual review" state.

## Connector-side outbound queue

When the connector is online and Tally is offline (Tally not running, company not open, etc.), the connector receives commands and queues them locally:

```sqlite
CREATE TABLE outbound_queue (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    command TEXT NOT NULL,
    args TEXT NOT NULL,             -- JSON
    idempotency_key TEXT NOT NULL,
    enqueued_at TIMESTAMP NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE UNIQUE INDEX idx_outbound_idem ON outbound_queue (idempotency_key);
```

A background thread in the connector polls Tally's reachability every 5 seconds. When Tally returns, the connector drains the queue in FIFO order, sending `command_result` for each as if it had just executed.

The backend-side timeout (`timeout_seconds`) does not apply to the queued case — the connector drops the original `request_id`'s response and starts emitting fresh ones once it can. The backend Celery task for those vouchers will have already failed and retried; when the next retry arrives, the connector's idempotency cache prevents double-posting.

## Heartbeat-driven status

`GET /api/v1/connector/status` returns:

- `connected: true` if `active_connectors[company_id]` exists AND `last_heartbeat_at < 90 seconds ago`.
- `tally_running` from the most recent heartbeat or register message.
- `queued_outbound_count` from the most recent heartbeat (the connector reports its local queue depth).
- `last_seen_at` from `last_heartbeat_at`.

If the connector has never connected, the status returns `connected: false` and `last_seen_at: null`.

## Security properties

1. **Connector token bound to one company.** A token issued for company A cannot be used to register as company B (the X-Company-ID header is checked against the token's `company_id`).
2. **Backend never trusts connector-supplied `company_id` in command results.** The result's `request_id` is matched against the pending command, which was issued for a known company.
3. **Connector verifies command's `company_id`.** Defense in depth — even if the backend has a routing bug, the connector won't execute a command for the wrong company.
4. **Idempotency at both layers.** Backend deduplicates HTTP requests (`IDEMPOTENCY.md`); connector deduplicates Tally posts (local SQLite cache). Network retries between the two layers don't double-post.
5. **All audit events from connector operations carry `source: "connector"`** so the audit trail distinguishes them from API and worker events.

## Forbidden patterns

- **Backend posting directly to Tally.** Backend code never opens an HTTP client to a customer's Tally instance. All Tally interaction goes through the connector.
- **Connector calling backend HTTP endpoints during normal operation.** The connector uses WS for everything except enrollment (one-time HTTP exchange) and token refresh (rare HTTP exchange when token is near expiry).
- **Storing customer data on the connector beyond the local queue.** Voucher data passes through; it is not retained after successful post (queue row deleted).
- **Connector executing commands without verifying `company_id`.** Defense in depth.
- **Re-using `request_id` across reconnects.** Each WS lifetime starts fresh.

## Patchable singletons

The connector subsystem leans on a few process-wide singletons —
`connector_registry.get_registry()`, the voucher dispatcher, and
(future) external clients like the FCM/APNs senders. Tests routinely
monkeypatch these to inject fakes and assert behaviour without a real
WebSocket or HTTP round-trip.

For the patches to actually take effect, the consuming module must
look the symbol up **lazily**, via the parent module, every call:

```python
# DO — the patch on connector_registry.get_registry is observed
from app.services.tally import connector_registry as _connector_registry_mod
...
registry = _connector_registry_mod.get_registry()

# DON'T — the patch is invisible to this module after import
from app.services.tally.connector_registry import get_registry
...
registry = get_registry()
```

The second form binds `get_registry` to the importing module's
namespace at import time; a later `monkeypatch.setattr(
connector_registry, "get_registry", fake)` only updates the original
module's attribute, not the bound name. Tests that look correct then
silently exercise the real singleton.

**The rule:** anything that's monkey-patched in tests — registry,
dispatcher, external client factories, time / clock providers —
imports the module, not the symbol. Exception classes and pure type
aliases are exempt because tests rely on class identity for `except`
and types aren't patched.

Audited sites that follow the rule today:
`app/services/dashboard_service.py`,
`app/api/v1/connector.py`,
`app/api/v1/connector_ws.py`,
`app/services/tally/voucher_dispatcher.py`,
`app/workers/posting_tasks.py`,
`app/services/voucher_service.py` (already used function-local
imports for the same reason).

## Test cases the human runs during validation

Validation report includes:

1. Install the connector on a Windows VM with TallyPrime running. Verify it registers within 5 seconds.
2. Stop Tally. Verify the connector reports `tally_running: false` in the next heartbeat.
3. POST a voucher via API while Tally is stopped. Verify:
   - Voucher exists in DB with `tally_posted_at: null`.
   - Audit row `voucher.tally_post_failed` exists with `code=connector_offline` (or `code=tally_unreachable`).
   - The Celery task is in retry queue.
4. Start Tally. Verify the next retry succeeds and voucher gets `tally_posted_at` set.
5. Disconnect the network on the connector PC for 2 minutes. Verify:
   - Backend marks `connected: false` after 90s.
   - Connector reconnects automatically when network returns.
6. POST the same voucher twice with the same Idempotency-Key. Verify only one Tally post happens (check Tally voucher list).
7. POST a voucher with a `ledger_name` that doesn't exist in Tally. Verify:
   - `command_result` returns `error.code=ledger_not_found`.
   - Audit row records the error.
   - Voucher stays in DB; user is notified to sync_masters or correct the ledger.
8. Trigger `sync_masters`. Verify:
   - All ledgers from Tally appear in the `ledgers` table.
   - Re-running sync doesn't duplicate ledgers.
9. Issue a `command` from the backend with the wrong `company_id`. Verify the connector rejects it and logs the attempt.

If any check fails, the connector subsystem has a defect. The phase does not pass.
