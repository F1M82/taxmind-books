# mypy Type Debt — Phase 0 Baseline

Catalogue of all 29 errors surfaced by the CI mypy step. Advisory only — CI gates this with `|| true` (see `.github/workflows/ci.yml` job `type-check`), so the build stays green. This file exists so a future fix sweep can target whole classes at once without rediscovery.

**Command:** `python -m mypy app --ignore-missing-imports --no-incremental`, run from `backend/`.
**Source:** `validation/phase_0_20260513_070509.md` §2, commit `2a0a02a`.

## Scope of this catalogue

mypy is invoked against `backend/app` only. The other tree paths the prompt asked about do not appear here because mypy never opens them:

| Bucket | Count | Why |
|---|---:|---|
| Inherited from salvage (`salvage/tally_client.py`) | 0 | mypy is not pointed at `salvage/`; the directory is read-only reference material (ruff also has `salvage/** = ["S314", "ALL"]` per-file-ignore). |
| In test files (`backend/tests/**`) | 0 | mypy command targets `app` only. `pyproject.toml` also relaxes strict rules for tests via the `tests.*` override. |
| In production code (`backend/app/**`) | 29 | All entries below. |
| Other | 0 | — |

## Production-code errors, by category

### `unused-ignore` — stale `# type: ignore` comments (11)

Each line carries a `# type: ignore[...]` that mypy now considers redundant — either the underlying error was fixed elsewhere or the comment was overly broad. Mechanical to clean up (delete the comment).

- `app/config.py:80` — stale ignore on settings field
- `app/core/audit.py:205` — stale `[return-value]` ignore on `_walk` return (paired with the `no-any-return` below)
- `app/core/money.py:67` — stale ignore in money primitive
- `app/core/security.py:282` — stale ignore in token issuance
- `app/services/account_lifecycle_service.py:364` — stale ignore in deletion worker hook
- `app/services/voucher_service.py:229` — stale ignore in voucher create
- `app/services/voucher_service.py:230` — stale ignore in voucher create (adjacent line)
- `app/api/deps.py:242` — stale ignore on tenancy dependency
- `app/api/v1/onboarding.py:44` — stale ignore on onboarding route
- `app/api/v1/auth.py:61` — stale ignore on `login` route hook
- `app/api/v1/auth.py:83` — stale ignore on `refresh` route hook

### `arg-type` — argument type mismatches (8)

Real type mismatches mypy refuses to coerce. Each requires a code change, not a comment.

- `app/core/security.py:140` — `token_type="access"` passed as `str` where `Literal["access", "refresh"]` expected (literal narrowing)
- `app/core/security.py:156` — same pattern for `"refresh"`
- `app/api/deps.py:77` — `decode_token(expected_type=...)` called with `str`, signature wants `Literal["access","refresh"] | None`
- `app/api/errors.py:144` — `Sequence[Any]` passed where `list[Any]` required by `_serialize_validation_errors`
- `app/api/errors.py:271` — `add_exception_handler(DomainException, ...)`: handler signature doesn't match Starlette's broader `Exception` contract
- `app/api/errors.py:273` — same Starlette signature gap for `RequestValidationError`
- `app/api/errors.py:275` — same Starlette signature gap for `HTTPException`
- `app/services/tally/voucher_dispatcher.py:130` — `dict(list[Row[tuple[UUID, str]]])` — Row isn't seen as iterable-of-tuples; needs `.tuples()` or explicit unpack

### `no-untyped-def` — missing parameter / return annotations (5)

Functions without complete signatures. Add the missing annotations.

- `app/services/account_lifecycle_service.py:362` — helper missing parameter type
- `app/services/voucher_service.py:225` — voucher service helper missing parameter type
- `app/api/deps.py:237` — function missing return type
- `app/api/v1/auth.py:57` — `login` handler missing parameter type
- `app/api/v1/auth.py:79` — `refresh` handler missing parameter type

### `untyped-decorator` — Celery `@shared_task` strips return type (2)

`celery.shared_task` has no stubs and returns `Any`, so decorated tasks become untyped. Fix path: type the decorator wrapper or add a stub. Affects only Celery tasks.

- `app/workers/lifecycle_tasks.py:28` — `process_due_account_deletions` (account-deletion worker)
- `app/workers/posting_tasks.py:25` — `post_voucher_to_tally` (Tally dispatch worker)

### Other (3)

Three one-off categories, one error each.

- `app/core/logging.py:42` — `no-untyped-call`: `_JsonFormatter` constructor is itself untyped; annotate the formatter class.
- `app/core/audit.py:205` — `no-any-return`: `_walk(value)` returns `Any` but enclosing function is annotated `dict[str, Any] | None`; needs a cast or narrowed return.
- `app/services/notification_service.py:85` — `assignment`: `r = _fcm_mod.send_push(...)` reuses a name bound to `ApnsResult` earlier in the branch; FCM and APNs result types should share a `Protocol` or be assigned to distinct locals.

## Summary

| Category | Count | Effort |
|---|---:|---|
| `unused-ignore` | 11 | Trivial — delete comments |
| `arg-type` | 8 | Real fixes (narrow types, adjust signatures) |
| `no-untyped-def` | 5 | Trivial — add annotations |
| `untyped-decorator` | 2 | Needs Celery stub or wrapper |
| Other | 3 | Per-case |
| **Total** | **29** | — |

Roughly half (16 of 29) are mechanical. The remainder are either real signature mismatches (`arg-type`) or library-stubs gaps (`untyped-decorator`).
