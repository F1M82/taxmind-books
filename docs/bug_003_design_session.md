# BUG-Books-003 — Direction B Design Session (Redis pub/sub fan-out)

**Date:** 2026-06-14
**Status:** DESIGN ONLY — no code, no commits, no tests run.
**Bug:** The `connector_registry` is process-local to the API uvicorn. The
Celery worker process gets a fresh, empty registry via `get_registry()`, so
every voucher dispatched through the worker path raises `ConnectorOffline`
regardless of whether the connector is actually connected. Production voucher
posting works today only in single-process EAGER mode (the BUG-005-validated
dev escape hatch).
**Direction:** B — Redis pub/sub fan-out keyed by `company_id`, as promised by
the docstring at `connector_registry.py:12-15`.

---

## ⚠️ CRITICAL FINDING — surfaced before design proceeds (guardrails 3 + 4)

**The connector does not implement idempotency-key dedup.** The protocol
document (`docs/CONNECTOR_PROTOCOL.md:326, 457, 808-818`) specifies a 24-hour
`(command, idempotency_key) → result` SQLite cache and an `outbound_queue`
table with a `UNIQUE INDEX` on `idempotency_key`. **Neither exists in the
connector code.** `connector/connector/message_handlers.py:73-77`
(`_handle_post_voucher`) calls `tally.post_voucher(voucher)` directly;
`dispatch_command` (lines 153-225) never reads `payload["idempotency_key"]`.
A grep of `connector/` for `idempotency|dedup|_cache` returns zero matches.

**Why this is load-bearing for Direction B.** Direction B makes automatic
retry a *first-class* behaviour: Celery `autoretry_for` on the worker task,
task redelivery on worker death (`task_acks_late=True` is already set), and
retry-after-timeout when a result is lost in the Redis transport. Every one of
those retries re-publishes the same `post_voucher` command. The standard
mitigation — "retry is safe because the connector dedups on `idempotency_key`"
— **does not hold against the current connector.** Each retry would post a
**duplicate voucher** to Tally. This is exactly the BUG-Books-001 duplicate
risk, but Direction B converts it from a rare PowerShell-BOM edge case into an
automatic, systemic behaviour on every transport hiccup.

**The deep point (guardrail 3):** safe automatic retry of `post_voucher`
*fundamentally requires* connector-side idempotency, because **only the
connector knows whether Tally accepted a given `idempotency_key`.** Backend-side
dedup (a Redis SETNX guard) can prevent the backend from double-*publishing*,
but it cannot resolve the "command reached Tally, result was lost, did Tally
actually post?" ambiguity (`TallyAmbiguousResponse`). After an ambiguous
response, only the connector's local cache can answer "already posted —
here is the cached GUID" vs "never posted — safe to re-send."

**Consequence for scope (guardrails 1 + 6):** implementing connector-side
idempotency is work in `connector/`, which is **outside** the
`backend/app/services/tally/` + `backend/app/workers/` scope of Direction B,
and it adds sessions to the 8-13 arc. This finding is escalated in
§"Open questions surfaced" as **BLOCKING-1**. The design below proceeds, but
every retry-based mitigation in Task 3 is explicitly marked as *conditional on
BLOCKING-1*.

---

## Foundational facts (re-confirmed from the chain map this session)

- `ConnectorRegistry` is a module-singleton (`connector_registry.py:257-264`),
  one instance per Python process. `_by_company: dict[UUID, ConnectorConnection]`.
- The WS handler (`connector_ws.py:96-109`) calls `registry.register(conn)` on
  handshake and `registry.deregister(conn)` in `finally`. The `ConnectorConnection.ws`
  is a `fastapi.WebSocket` bound to the API process's event loop.
- The dispatcher (`voucher_dispatcher.py:250-481`) is **transport-agnostic**: it
  resolves `registry = registry or get_registry()` (line 265) and calls
  `registry.send_command(...)` (line 368). It does not care how the command
  reaches the connector. **This is the seam Direction B exploits — call sites
  do not change.**
- `enqueue_voucher_post` (`voucher_dispatcher.py:112-157`) has three branches:
  `TAXMIND_SKIP_TALLY_DISPATCH` (test), `CELERY_TASK_ALWAYS_EAGER` →
  `_enqueue_in_process` (in-process asyncio task — the BUG-005-validated dev
  path), else `post_voucher_to_tally.delay()` (the worker path — the BUG-003
  path).
- The worker task (`posting_tasks.py:38-84`) already has
  `autoretry_for=(ConnectorOffline, CommandTimeout, TallyRetryableEnvelope)`,
  `task_acks_late=True`, `max_retries=5`. It is wired but dormant (EAGER routes
  around it).
- **No direct Redis client exists** in `backend/app/`. Redis is reached only as
  Celery's broker/backend transport. `redis-py` is a transitive dependency.
- **No pub/sub anywhere** today. Direction B is the first.
- `main.py` has **no lifespan / startup hook** (confirmed: zero matches for
  `lifespan|startup|on_event`). Relevant to guardrail 1 — we must avoid forcing
  one.
- The connector command/result envelope shape is fixed by `CONNECTOR_PROTOCOL.md`
  and the existing WS code: `{type, request_id, ts, payload}`, with command
  payload `{company_id, command, args, timeout_seconds, idempotency_key}` and a
  `command_result` payload `{command, status, result|error, duration_ms,
  retryable?}`.

---

# Task 1 — Publish/subscribe contract

## 1A. Who publishes and who subscribes

**Options.**

1. **Worker-publishes-command / owning-API-publishes-result** (canonical
   fan-out). The process that wants to dispatch (the Celery worker) publishes a
   command to a per-company channel. The API process that *owns the connector
   WS* is subscribed, relays the command to Tally via its local `send_command`,
   and publishes the result back. The worker awaits the result.
2. **API-only, drop the worker.** Do all dispatch inline in the request handler
   (generalised EAGER). Rejected: this only works if the API instance handling
   the POST is the *same* instance that owns the connector WS. The moment the
   backend runs more than one API instance, a POST landing on instance B cannot
   reach a connector owned by instance A — the same cross-process gap, just
   moved from worker↔API to API↔API. Pub/sub is still required.
3. **Internal HTTP hop (memory option 3).** Worker calls
   `POST /internal/voucher-dispatch/<id>` on the owning API. Rejected: the
   caller must *know which instance owns the WS*, which needs a routing
   table/registry anyway — and that routing table is the very thing pub/sub
   gives you implicitly (subscription = ownership). HTTP hop also requires a new
   API route + service-account auth (guardrail-1 structural change to routes).

**Recommendation: Option 1**, generalised. The *publisher* is "any process
needing to dispatch a command for company X" (the worker today). The
*subscriber* is "the single API process that currently owns company X's
connector WS." Subscription is established at `register()` and torn down at
`deregister()` — ownership and subscription are the same fact.

**Key design choice (preserves BUG-005, guardrail 2):** routing local-vs-remote
lives *inside* `ConnectorRegistry.send_command`. If `company_id` is in the local
`_by_company` dict, send over the local WS exactly as today (zero Redis). Only
when the company is *not* owned locally does `send_command` publish to Redis and
await. In EAGER/dev mode the API process owns the WS and runs the dispatch, so
`send_command` always takes the local branch — **Redis is never touched on the
dev path.** The relay subscription is itself gated on worker-mode (EAGER off),
so dev/test require no Redis at all.

## 1B. Channel naming and keyspace conventions

**Options.**

1. Per-company command channel + **per-request** result channel.
2. Per-company command channel + **per-company** result channel (awaiters
   filter by `request_id`).
3. Single global command channel (subscribers filter by company). Rejected
   immediately — breaks tenant isolation and fan-out efficiency.

**Recommendation: Option 1.**

- Command channel (one subscriber = owning API): `taxmind:tally:cmd:{company_id}`
- Result channel (one subscriber = the awaiting worker):
  `taxmind:tally:res:{request_id}`

Per-request result channels give correlation *for free* (the channel name is the
correlation key), naturally scope each awaiter to exactly its own reply, and
clean up trivially (subscribe → await → unsubscribe). Per-company result
channels (Option 2) force every awaiter to receive and discard other awaiters'
replies, which is wasteful and a minor cross-talk risk. Convention details in
Task 2.

## 1C. Message payload shape

Mirror the existing WS envelope (`{type, request_id, ts, payload}`) so the relay
is a thin translator, not a reshaper. JSON, compact separators (matches
`connector_ws.py:227` `json.dumps(..., separators=(",", ":"))`).

**Command envelope** (worker → `taxmind:tally:cmd:{company_id}`):

```json
{
  "v": 1,
  "kind": "command",
  "request_id": "<uuid4>",
  "company_id": "<uuid>",
  "command": "post_voucher",
  "args": { "...": "..." },
  "idempotency_key": "<voucher_id>",
  "timeout_seconds": 30,
  "reply_to": "taxmind:tally:res:<request_id>",
  "published_at": "<iso8601>"
}
```

**Result envelope** (owning API → `taxmind:tally:res:{request_id}`):

```json
{
  "v": 1,
  "kind": "result",
  "request_id": "<uuid4>",
  "status": "success" | "error" | "offline" | "timeout",
  "result": { "...": "..." },
  "error": { "code": "...", "message": "..." },
  "retryable": true,
  "duration_ms": 123,
  "published_at": "<iso8601>"
}
```

`v` is a protocol version int (forward-compat; reject mismatches loudly per
Constitution §4). `kind` guards against channel cross-wiring. The `status`
values `offline`/`timeout` are *transport-layer* statuses the relay synthesises
when the local `send_command` raises `ConnectorOffline`/`CommandTimeout` — so
the worker reconstructs the *same exception types* it would have seen
in-process, keeping the dispatcher's `except` tuples valid unchanged.

## 1D. Request/response correlation + awaiter mechanism

**`request_id` (uuid4) is the correlation key**, and because the result channel
is per-request, correlation is implicit in the subscription. The awaiter:

1. Worker generates `request_id`, computes `reply_to = taxmind:tally:res:{rid}`.
2. Worker **subscribes to `reply_to` first** (pub/sub has no persistence — you
   must be subscribed before the reply is published).
3. Worker publishes the command to `taxmind:tally:cmd:{company_id}`.
4. **If `PUBLISH` returns 0** (no subscriber → no API owns this connector) →
   raise `ConnectorOffline` immediately, unsubscribe. This is the offline-
   detection mechanism — no separate presence key needed for single-instance
   Phase 0.5.
5. Worker `await`s one message on `reply_to` with a timeout (Task 1E).
6. On message: parse, reconstruct exception or return payload, unsubscribe.
7. On timeout: unsubscribe, raise `CommandTimeout` (retryable — but see
   BLOCKING-1).

The owning-API relay side, established at `register()`:

1. On `register(conn)` (worker-mode only): start a background task subscribing
   to `taxmind:tally:cmd:{conn.company_id}`.
2. On each command: **spawn a per-command task** (do *not* block the
   subscription read loop on the Tally round-trip — see Task 3 §8), which calls
   the existing local `conn.send_command(...)`, then publishes the result to the
   command's `reply_to`.
3. Translate local exceptions → result envelope `status`:
   `ConnectorOffline → "offline"`, `CommandTimeout → "timeout"`,
   `TallyRetryableEnvelope → "error" retryable=true`,
   `TallyRejectedEnvelope → "error" retryable=false`.
4. On `deregister(conn)`: cancel the subscription task.

## 1E. Timeout semantics

Three nested layers, each strictly larger than the one it wraps:

| Layer | Where | Value | On expiry |
|---|---|---|---|
| Tally/WS command | owning-API local `send_command` `asyncio.wait_for` | 30s `post_voucher`, 120s `sync_masters` (unchanged) | future cancelled, relay publishes `status:"timeout"` |
| Worker Redis-await | worker awaiting `reply_to` | `command_timeout + 10s` transport grace (40s / 130s) | `CommandTimeout` raised (retryable*) |
| Celery time limit | worker task | `soft=worker_await+20s`, `hard=+40s` | task killed; redelivered (acks_late) |

The worker-await must exceed the connector-side timeout so that a legitimately
slow-but-successful Tally post returns a real result rather than racing the
worker's own timer. The 10s grace covers two Redis hops + relay scheduling.

**Cleanup on timeout.** Worker unsubscribes from `reply_to` (idempotent).
If the relay later produces a result after the worker gave up, it publishes to a
channel with no subscriber — `PUBLISH` returns 0, message dropped, harmless.
**The voucher's true Tally state is then unknown** and reconciliation depends on
retry + connector idempotency (BLOCKING-1). Without it, the timeout path is the
single largest duplicate-voucher exposure in the design.

*(\*) "retryable" here is conditional on BLOCKING-1. If connector-side
idempotency is not implemented, retry-on-timeout is **not** safe and the
default posture must change — see Task 3 and Open Questions.*

---

# Task 2 — Keyspace design

## Key naming conventions

All keys/channels carry the app prefix `taxmind:tally:` and a tenant or
request scope. Colon-delimited (Redis convention, plays well with `redis-cli
--scan` and keyspace tooling).

| Name | Type | Scope | Lifetime |
|---|---|---|---|
| `taxmind:tally:cmd:{company_id}` | pub/sub channel | per company | transient (subscription lives = WS owned) |
| `taxmind:tally:res:{request_id}` | pub/sub channel | per request | transient (subscribe→await→unsubscribe) |
| `taxmind:tally:owner:{company_id}` | string key (lease) | per company | **Phase 1 only** — deferred (see below) |
| `taxmind:tally:idem:{idempotency_key}` | string key (SETNX) | per voucher | **optional hardening** — see Idempotency |

## Transient (pub/sub-only) vs durable (key)

**Pub/sub-only (transient):** the command and result channels. Pub/sub is
fire-and-forget with no persistence — chosen deliberately (see "pub/sub vs
Streams" in Decision Summary). The transport carries no durability; durability
of the *intent* already lives in the DB (`status=pending_tally_post` +
`tally_post_attempts`) and re-dispatch is BUG-002's job. We do **not** want the
transport to independently persist commands, because a command persisted in a
Stream that re-fires later is a duplicate-post hazard that fights idempotency
and BUG-002's re-enqueue logic.

**Durable keys — minimised to near-zero for Phase 0.5:**

- No durable key is *required* for the single-API-instance Phase 0.5 topology.
  Offline detection = `PUBLISH` returning 0. Correlation = per-request channel.
- `owner:{company_id}` lease key is the multi-instance ownership arbiter
  (Phase 1) and is **explicitly deferred** (Task 3 §4, Open Questions).
- `idem:{idempotency_key}` is optional backend-side double-publish protection
  (see below).

## TTLs and cleanup

- Channels: no TTL (pub/sub channels are not keys; they vanish when the last
  subscriber unsubscribes).
- `owner` lease (Phase 1): short TTL, 15-30s, refreshed by the owning instance
  on each connector heartbeat (`_handle_heartbeat`, every ≤30s per protocol).
  Expiry = ownership released, another instance may claim.
- `idem` key (if adopted): TTL = max retry window + margin (e.g. 15 minutes;
  must exceed `retry_backoff_max=600s`). Auto-expiry is the only cleanup;
  no sweeper.

## Multi-tenancy

Every channel and key is **company-scoped or request-scoped by construction.**
A worker dispatching for company A publishes to `cmd:{A}` and can never receive
`cmd:{B}` traffic. The relay subscribes to exactly one company's channel per
connection. There is no shared channel on which cross-tenant leakage could
occur. The connector additionally re-checks `company_id` against its registered
company (`message_handlers.py:168-173`, `CompanyMismatch`) — defence in depth.

## Idempotency — what we offer and how

**Delivery guarantee:** at-least-once. A command may be published/attempted more
than once (Celery autoretry, task redelivery on `acks_late`, retry-after-timeout).

**Effect guarantee — THIS IS THE GAP (BLOCKING-1):** the *intended* contract,
per `CONNECTOR_PROTOCOL.md:457`, is exactly-once *effect* via connector-side
`(command, idempotency_key)` dedup. **That dedup is not implemented in the
connector today.** Until it is, Direction B can only honestly offer:

- **Backend double-publish protection (optional, in-scope):** before publishing
  a command, `SET taxmind:tally:idem:{idempotency_key} <request_id> NX EX 900`.
  If the key already exists, a publish for this voucher is already in flight —
  skip or short-circuit. This prevents the backend from emitting two *concurrent*
  publishes for the same voucher (e.g. a double-enqueue). It does **NOT** make
  retry-after-Tally-dispatch safe, because the backend cannot know whether the
  first attempt's Tally write landed. Only the connector can.

**Net:** the keyspace design is idempotency-*ready* (the `idem` key is cheap and
in-scope), but true retry-safety is blocked on connector work outside this
scope. We must not advertise exactly-once until BLOCKING-1 is resolved.

---

# Task 3 — Race conditions and failure modes

Severity legend: **MUST** = solve before worker-mode is enabled in any
environment that touches real Tally; **DEFER** = acceptable risk for the
Phase 0.5 arc with the stated assumption documented.

### 1. API dies after consuming command, before publishing result
- **Bad outcome:** worker awaits, hits `CommandTimeout`. The command may or may
  not have reached Tally. Worker retries → **duplicate post** unless connector
  dedups.
- **Mitigation:** retry + connector idempotency. *Conditional on BLOCKING-1.*
  Without it, this is a live duplicate-voucher path.
- **MUST** (the mechanism must exist; today it does not).

### 2. API dies mid-Tally-dispatch (command sent to Tally, no result back)
- **Bad outcome:** identical to §1 — the WS future dies with the process; the
  Tally write status is unknown (`TallyAmbiguousResponse` territory).
- **Mitigation:** same as §1. Only the connector can resolve "did Tally post?"
  via its cache. *Conditional on BLOCKING-1.*
- **MUST.**

### 3. Worker dies between publishing command and receiving result
- **Bad outcome:** the relay completes the Tally post and publishes the result
  to a `reply_to` with no subscriber → result dropped. **Tally shows posted, DB
  still says `pending_tally_post`** — divergence.
- **Mitigation:** `task_acks_late=True` (already set) means the broker
  redelivers the task to another worker. The redelivered task re-dispatches;
  *if* the connector dedups it returns the cached success (with GUID) and the DB
  converges to `posted`. **Convergence depends on the connector returning a
  cached SUCCESS envelope (with GUID), not a "duplicate" error.** *Conditional
  on BLOCKING-1.* Without dedup, the redelivery double-posts.
- **MUST.**

### 4. Two API instances both think they own the same connector (split-brain)
- **Bad outcome:** both subscribe to `cmd:{company_id}`, `PUBLISH` returns 2,
  both relays send to their (possibly stale) WS → double-post and/or one sends
  to a dead socket.
- **Mitigation:** **cannot occur in single-API-instance Phase 0.5** (there is
  exactly one API process; the connector dials one instance). For Phase 1
  horizontal scaling, an `owner:{company_id}` lease (SET NX + TTL, refreshed on
  heartbeat) arbitrates: only the lease-holder runs the relay. The
  local-vs-remote `send_command` seam means adding the lease later does not
  touch call sites.
- **DEFER** (Phase 1). **Deployment constraint to record:** Direction B as
  scoped here is correct **only for a single API instance**. Running multiple
  API replicas before the lease lands re-introduces BUG-003 as a duplicate-post
  bug. Surfaced in Open Questions as a topology commitment (guardrail 5).

### 5. Connector disconnects mid-command (relay received publish, before/during WS send)
- **Bad outcome:** if unhandled, the relay task raises and no result is ever
  published; worker times out with no signal.
- **Mitigation:** local `send_command` already raises `ConnectorOffline` (no
  entry in `_by_company`) or the future is failed by `cancel_pending()` on
  deregister (`connector_registry.py:168-173`). The relay **must catch these and
  publish a `status:"offline"` result** so the worker fails fast and retries
  (rather than waiting out the full timeout). This is core relay logic, not an
  add-on.
- **MUST** (pure relay correctness; no connector dependency).

### 6. Redis itself becomes unavailable
- **Bad outcome:** worker cannot publish; relay subscription drops. Dispatch
  stalls.
- **Mitigation:** worker publish failure → retryable error → Celery backoff
  (broker is also Redis, so if Redis is fully down Celery isn't running tasks
  anyway — consistent failure, not corruption). The relay must **reconnect with
  backoff** and **must not couple Redis health to WS health**: the WS receive
  loop and `register/deregister` must keep working even if the relay
  subscription is temporarily down (a connector staying connected is more
  valuable than the relay being live; commands just report offline meanwhile).
  EAGER/dev path is unaffected (no Redis).
- **MUST** (decoupling relay health from WS health is a design rule, cheap to
  honour, expensive to retrofit).

### 7. Command published to a company with no subscriber (no API owns that connector)
- **Bad outcome:** with naive pub/sub the command silently vanishes.
- **Mitigation:** `PUBLISH` return value = number of receivers. **0 ⇒ raise
  `ConnectorOffline` immediately** (worker emits `voucher.tally_post_queued`,
  stays `pending_tally_post`, BUG-002 re-enqueue applies). This is the
  intentional offline semantics and matches today's behaviour exactly.
- **MUST** (it is the core offline-detection mechanism — and it is cheap).

### 8. Slow consumer / pub-sub backlog
- **Bad outcome:** Redis enforces `client-output-buffer-limit pubsub`; a relay
  that reads its subscription slowly can be force-disconnected by Redis, killing
  the relay for that company.
- **Mitigation:** the relay's subscription read loop must **never block on the
  Tally round-trip** — it dispatches each command to a separate task and
  immediately returns to reading (Task 1D step 2). At Phase 0.5 volumes (one
  message per voucher, human-paced) backlog risk is negligible. Backpressure
  tuning and buffer-limit configuration are **DEFER**.
- **MUST** (the "don't block the read loop" architecture); **DEFER** (tuning).

### Cross-cutting note
Failure modes §1, §2, §3 all collapse to the same root requirement: **safe
automatic retry requires connector-side idempotency.** That is BLOCKING-1. The
relay-correctness modes (§5, §6, §7, §8) are independent of it and are squarely
in-scope and solvable now. §4 is a deferred topology concern.

---

# Task 4 — Test strategy

Today: **zero** multi-process coverage. `conftest.py` sets
`TAXMIND_SKIP_TALLY_DISPATCH=1`, so the default posture skips dispatch
entirely; the dispatcher tests in `test_posting_task.py` exercise
`dispatch_voucher_to_tally` directly with a `_FakeRegistry` in a single process.
Nothing boots a worker, a real WS, or asserts cross-process registry isolation.

## The keystone decision: fakeredis, not a real Redis container

**Use `fakeredis` (with `aioredis`/`redis.asyncio` support) as the default test
backend.** fakeredis runs in-process, supports pub/sub between multiple client
connections to one shared `FakeServer`, and requires **zero manual setup** — no
container, no port, no teardown churn. This directly applies the BUG-005
operational lesson (no manual-environment tax in the test loop). A real Redis is
gated behind an opt-in `@pytest.mark.real_redis` marker for CI only.

**Simulating "two processes" deterministically:** instantiate two registry
objects in one test process, both pointing at the same `FakeServer`:
- "API process" → a `ConnectorRegistry` with a fake connector registered and a
  running relay subscription.
- "Worker process" → the remote `send_command` path (no local connector).
Drive a command worker→relay→fake-WS→result and assert the round-trip. Because
they share one `FakeServer`, pub/sub crosses the "process" boundary exactly as
it would across real processes, but synchronously and deterministically.

## Test layers

**Unit (no Redis at all):**
- Channel-name construction (`cmd:{company_id}`, `res:{request_id}`).
- Envelope serialise/deserialise round-trip; `v`/`kind` validation; rejection of
  malformed envelopes.
- The local-vs-remote routing decision in `send_command` (company-owned-locally
  → local branch; not-owned → remote branch) using a stub publisher.
- Timeout arithmetic (worker-await > connector-timeout invariants).

**Integration (fakeredis):**
- Happy path: command published, relay dispatches to a fake `ConnectorConnection`
  whose `send_command` returns a canned success, result received, dispatcher
  stamps `tally_posted_at`/GUID.
- `PUBLISH` returns 0 → `ConnectorOffline` (no relay subscribed).
- Relay translates local `ConnectorOffline`/`CommandTimeout`/`TallyRejectedEnvelope`/
  `TallyRetryableEnvelope` into the right result `status` and the worker
  reconstructs the matching exception.
- Relay does not block its read loop (dispatch two commands; the second must be
  read before the first's slow stub completes).

**Failure-mode tests (deterministic simulation):**
- **"API dies mid-dispatch"** → do not start (or cancel) the relay after the
  command is published; assert the worker-await hits `CommandTimeout`, the
  voucher stays `pending_tally_post`, and `tally_post_attempts` increments.
  ("Death" = the relay simply never publishes a result.)
- **"Worker dies before result"** → publish the command, drive the relay to
  completion, but unsubscribe the worker's `reply_to` before the result is
  published; assert `PUBLISH` of the result returns 0 and the relay swallows it
  cleanly (no crash). DB-convergence-on-redelivery is asserted *only once
  BLOCKING-1 lands* (it depends on connector dedup).
- **"Connector disconnects mid-command"** → fake `send_command` raises
  `ConnectorOffline`; assert relay publishes `status:"offline"` and the worker
  re-raises `ConnectorOffline` fast (not after full timeout).
- **"Redis down"** → point the client at a `FakeServer` that raises on
  publish/subscribe; assert worker dispatch fails retryably and the EAGER path
  is wholly unaffected (a parallel EAGER test must still pass with the same
  broken Redis).

## Integration vs unit line

The line falls at **"does this test need the Redis transport?"** Envelope shape,
channel naming, and routing logic are unit (pure functions / stubbed publisher).
Anything asserting a message actually crosses a channel is integration on
fakeredis.

## What is NOT tested (acceptable deferrals)

- Real Redis cluster / Sentinel / failover behaviour.
- True OS-process death (we simulate via "relay never publishes" / "worker
  unsubscribes"); no real subprocess kill.
- Real network partitions, Redis `client-output-buffer-limit` eviction under
  load.
- Split-brain across multiple API instances (no Phase 0.5 code exists for it).
- Real connector `.exe` + real Tally end-to-end — that is deferred operational
  verification, mirroring BUG-005's "first real-user encounter" posture.

## How existing `test_posting_task.py` tests adapt to Direction B

**They do not change.** Those tests call `dispatch_voucher_to_tally(...)`
directly with an injected `_FakeRegistry`. Because Direction B keeps the
dispatcher transport-agnostic (the registry's `send_command` is still the
boundary, line 368), `_FakeRegistry` continues to stand in for the registry
regardless of whether the real registry would have gone local or remote. The
existing 9 tests remain valid as-is. Direction B's new behaviour (publish/await,
relay) is covered by **new** test modules
(`tests/integration/services/tally/test_redis_fanout.py` or similar), not by
mutating the dispatcher tests. This is a deliberate strength of the "don't
change call sites" seam: the highest-value existing tests are insulated from the
transport rework.

---

# Decision summary

| # | Decision | One-line justification |
|---|---|---|
| D1 | **Worker publishes command, owning-API publishes result** | Subscription = ownership; pub/sub does the instance-routing HTTP-hop would need a table for. |
| D2 | **Route local-vs-remote inside `send_command`** | Call sites unchanged; EAGER path stays 100% local/Redis-free → BUG-005 preserved (guardrail 2). |
| D3 | **Redis pub/sub primitives, NOT Streams** | Transient command/result transport; `PUBLISH`-count gives free offline detection; Streams' durability would re-fire commands and fight idempotency/BUG-002. |
| D4 | **`redis.asyncio` (bundled in redis-py ≥4.2)** | redis-py already transitively present (Celery transport); async-native for the API event loop + worker `asyncio.run`; `aioredis` is deprecated/merged. |
| D5 | **JSON serialization, compact separators** | Matches existing WS envelopes exactly; inspectable via `redis-cli`/`MONITOR`; no new dependency. msgpack's size win is irrelevant at this volume. |
| D6 | **Channels `taxmind:tally:cmd:{company_id}` / `res:{request_id}`** | App-prefixed, tenant/request-scoped by construction; no cross-tenant channel. |
| D7 | **Per-request result channels** | Correlation for free; clean subscribe→await→unsubscribe lifecycle. |
| D8 | **Offline = `PUBLISH` returns 0** | No presence key needed for single-instance Phase 0.5; matches today's immediate-offline semantics. |
| D9 | **Timeouts: connector 30/120s; worker-await = +10s; Celery soft +20s/hard +40s** | Each layer strictly wraps the next so a slow-but-successful post returns a real result, not a self-inflicted timeout. |
| D10 | **Relay gated on worker-mode (EAGER off); lazy Redis pool in `services/tally`** | Dev/test need no Redis; avoids a `main.py` lifespan hook (guardrail 1). |
| D11 | **fakeredis as default test backend; real Redis CI-only opt-in** | Zero manual-setup tax (BUG-005 lesson); deterministic two-"process" simulation in one test process. |
| D12 | **Relay read loop never blocks on Tally; one task per command** | Prevents pub/sub buffer-limit eviction (Task 3 §8). |
| D13 | **Optional backend `idem:{key}` SETNX guard** | Cheap double-publish protection; explicitly NOT a substitute for connector dedup. |

---

# Open questions surfaced (guardrail stops)

**BLOCKING-1 — Connector-side idempotency is specified but not implemented
(guardrails 3 + 4).** `CONNECTOR_PROTOCOL.md:326,457,808-818` promises a 24h
`(command, idempotency_key)` SQLite dedup cache + `outbound_queue` table;
`connector/connector/message_handlers.py` implements neither. **Safe automatic
retry of `post_voucher` is impossible without it** — only the connector can
answer "did Tally already accept this key?". Every retry-based mitigation in
Task 3 (§1/§2/§3) is blocked on this. **This is connector work, outside the
`backend/app/services/tally` + `workers` scope (guardrail 1) and adds to the
8-13 arc (guardrail 6).** *Reviewer decision required:* (a) implement connector
idempotency as a Direction-B prerequisite (recommended; it is the honest
fix), (b) ship Direction B with a strict **no-auto-retry / at-most-once**
posture for `post_voucher` (retries become manual operator actions — large
behavioural change, conflicts with the existing `autoretry_for` tuple), or
(c) accept duplicate-post risk on transport failures as known debt (not
recommended — it is a financial-data correctness bug).

**BLOCKING-2 — Single-API-instance topology commitment (guardrail 5).**
Direction B as designed is correct only for **one** API process. Running
multiple API replicas before the Phase-1 `owner:{company_id}` lease lands turns
BUG-003 into a duplicate-post split-brain bug (Task 3 §4). *Reviewer should
confirm* the Phase 0.5 deployment is single-API-instance and that this
constraint is acceptable to record as an explicit operational guard (e.g. a
deploy-time assertion / runbook note). The design is forward-compatible (the
lease slots into the `send_command` seam) but the constraint is real and
load-bearing.

**OPEN-3 — Connector dedup return semantics (depends on BLOCKING-1's
resolution).** Even once dedup exists, convergence in Task 3 §3 requires a
duplicate command to return a **cached SUCCESS envelope with the original GUID**,
not a `duplicate`/`conflict` error. The protocol text (line 457) implies this
("returns the cached result") but the exact envelope shape for a dedup-hit must
be pinned so the dispatcher stamps the GUID correctly. Non-blocking for design,
must be nailed before implementing the relay's success path.

**Note on guardrail 1 (assessed, NOT blocking):** Direction B adds the
`redis.asyncio` import surface, which means a dependency-manifest change
(`pyproject.toml`/`requirements`) outside the two target dirs. Assessed as
**non-material** — it is a dependency declaration, not a structural change to
routes/models/middleware. The Redis connection pool is lazy-initialised inside
`services/tally` (no `main.py` lifespan hook). Flagging for transparency; not
treating as a stop.

---

# Implementation outline (phasing the 8-13 session arc)

Each step is a discrete, independently-reviewable commit. Steps are ordered so
the dev/EAGER path is never broken at any commit boundary.

0. **(today) Design session** — this document. ✅
1. **Redis client foundation** — add `redis.asyncio` dep; lazy `get_redis()`
   pool accessor in `services/tally`; connection/backoff handling; unit test for
   pool lifecycle. (~1 session)
2. **Envelope + channel module** — dataclasses, JSON (de)serialisation,
   channel-name helpers, `v`/`kind` validation; pure unit tests. (~1 session)
3. **Remote command client (worker side)** — subscribe `reply_to` → publish
   `cmd` → `PUBLISH==0` offline detection → await with timeout → reconstruct
   exception/return; fakeredis unit tests. (~1-2 sessions)
4. **Relay (owning-API side)** — subscribe `cmd:{company_id}` on `register`
   (worker-mode gated), per-command task, exception→status translation, publish
   to `reply_to`, cancel on `deregister`; fakeredis tests incl. §5/§6/§8. (~2 sessions)
5. **Wire local-vs-remote routing into `ConnectorRegistry.send_command`** —
   local branch unchanged (EAGER preserved), remote branch via step 3 client;
   verify EAGER + SKIP paths untouched. (~1 session)
6. **Failure-mode integration suite** — deterministic "API dies" / "worker
   dies" / "Redis down" tests on fakeredis. (~1-2 sessions)
7. **[BLOCKING-1] Connector idempotency** — implement the spec'd SQLite dedup
   cache in the connector; *or*, per reviewer decision, adopt the no-auto-retry
   posture and adjust `posting_tasks.autoretry_for`. **Scope/owner TBD by
   reviewer — may fall outside this arc.** (~1-3 sessions if done here)
8. **Enable worker-mode dispatch** — flip the deploy off EAGER for a
   worker-mode environment; confirm `posting_tasks` autoretry aligns with the
   BLOCKING-1 decision. (~1 session)
9. **Deferred operational verification** — real Redis + connector + Tally,
   first-real-user posture (mirrors BUG-005). Not a coding gate. (~1 session)

**Scope assessment (guardrail 6):** steps 1-6 + 8 + 9 ≈ **8-10 sessions** and
fit the stated arc cleanly. **Step 7 (BLOCKING-1) is the swing factor:** if
connector idempotency must be built inside this arc, add ~1-3 sessions, pushing
toward/past the upper bound. If the reviewer scopes connector idempotency as a
separate prerequisite track, Direction B proper stays within 8-13. **This is the
cost-framing item the reviewer flagged as load-bearing — surfaced explicitly.**

---

# Operational lessons applied (from BUG-005 environment work)

- **No manual-setup tax in the test loop.** BUG-005's live validation burned
  three sessions on local env-orchestration (stale connector `.exe`, token
  churn, AV friction) and never reached the validation moment. Direction B's
  test strategy is built on **fakeredis** precisely so the entire fan-out is
  testable with zero manual Redis/container setup (D11). New pub/sub code must
  never *require* a developer to hand-stand-up Redis to run the suite.
- **Stale-artifact fragility.** BUG-005 was derailed by a connector `.exe` that
  predated the code under test. Direction B touches the connector only under
  BLOCKING-1; if it does, the implementation outline must include a connector
  **rebuild + mtime/version assertion** before any live check — do not repeat
  the stale-`.exe` trap.
- **Truncation discipline.** This very design used the temp-file workflow
  (`docs/bug_003_design_session.md`, built section-by-section) rather than
  emitting a giant terminal blob — same discipline BUG-005 adopted after its
  truncation pain.
- **Env hygiene.** Direction B keeps the dev path Redis-free (D10) so a
  developer's missing/!misconfigured Redis cannot silently break local voucher
  posting the way the EAGER/SKIP confusion did. The mode is explicit
  (`CELERY_TASK_ALWAYS_EAGER`) and the relay's Redis dependency is gated on it.
- **Defer live validation honestly.** Per the Phase-0 precedent, live
  real-Redis+connector+Tally verification is *deferred operational
  verification*, not a coding gate — but unlike BUG-005 it is backed by a real
  multi-"process" fakeredis suite, so the deferral is far better insured.

---

# What NOT to do (rejected ideas — do not re-litigate)

- **Do NOT use Redis Streams for the command/result transport.** Considered for
  durability/replay. Rejected: a persisted command that re-fires after the fact
  is a duplicate-post hazard that fights idempotency and BUG-002; consumer-group
  ops/trimming/claim complexity blows scope; we explicitly *want* "offline =
  no subscriber now," which `PUBLISH`-count gives for free. (D3)
- **Do NOT make EAGER mode go through Redis.** The in-process path must stay
  local and Redis-free — it is the BUG-005-validated dev escape hatch
  (guardrail 2). Routing lives in `send_command`; EAGER always hits the local
  branch. (D2/D10)
- **Do NOT add a `main.py` lifespan/startup hook for the Redis pool.** Lazy-init
  inside `services/tally` instead, to stay within the guardrail-1 scope. (D10)
- **Do NOT solve multi-instance split-brain now.** The `owner` lease is Phase 1;
  building it now blows scope and isn't needed for single-instance Phase 0.5.
  But DO record the single-instance deployment constraint (BLOCKING-2).
- **Do NOT rewrite `dispatch_voucher_to_tally` or the `test_posting_task.py`
  tests.** The dispatcher is transport-agnostic by design; changing it would
  discard the highest-value existing coverage for no benefit.
- **Do NOT assume backend-side dedup makes retry safe.** A Redis SETNX guard
  stops double-*publish* but cannot resolve post-dispatch Tally ambiguity. Only
  connector-side dedup does. Do not ship Direction B advertising exactly-once
  effect until BLOCKING-1 is resolved. (D13)
- **Do NOT introduce `aioredis`.** It is deprecated and merged into redis-py as
  `redis.asyncio`; adding it would be a second Redis client. (D4)
- **Do NOT use msgpack.** Opaque in `redis-cli`, new dependency, negligible size
  benefit at human-paced voucher volume. JSON matches the existing WS envelopes.
  (D5)

---

*End of design session. No code modified, no commits, no tests run.*
