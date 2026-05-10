"""Per-request idempotency handler.

Implements the contract from `docs/IDEMPOTENCY.md`: a client may send
an `Idempotency-Key` header on state-changing endpoints; the first
request stores the response, subsequent requests with the same key +
body get the stored response back without re-executing.

This module owns the table interaction (`idempotency_keys`); the API
layer wires it via `app.api.deps.get_idempotency_handler`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import SCOPE_BYPASS_OPTION
from app.models.idempotency_key import IdempotencyKey

DEDUP_WINDOW = timedelta(hours=24)
LOCK_TIMEOUT = timedelta(seconds=60)


# Error codes used in the detail bodies. The error-handling middleware
# in P0.13 maps these into the standard API envelope.
CODE_KEY_REQUIRED = "idempotency_key_required"
CODE_KEY_INVALID = "idempotency_key_invalid"
CODE_IN_PROGRESS = "idempotency_in_progress"
CODE_KEY_MISUSE = "idempotency_key_misuse"
CODE_REPLAY = "idempotency_replay"


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def is_valid_key(key: str) -> bool:
    """Validate the Idempotency-Key format from IDEMPOTENCY.md §Header."""
    if not (8 <= len(key) <= 255):
        return False
    return all(c.isprintable() and not c.isspace() and ord(c) < 128 for c in key)


def canonical_hash(body: bytes) -> str:
    """SHA-256 of the canonical JSON form of `body`.

    Empty body → hash of `{}`. Non-JSON body (multipart upload) → hash
    of the raw bytes (per spec).
    """
    if not body:
        return hashlib.sha256(b"{}").hexdigest()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return hashlib.sha256(body).hexdigest()
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _err(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {
        "error": {"code": code, "message": message}
    }
    if details:
        detail["error"]["details"] = details
    return HTTPException(
        status_code=status_code, detail=detail, headers=headers
    )


# ---------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------


class IdempotencyHandler:
    """One handler per request. Owns the lifecycle of a single key."""

    def __init__(
        self,
        request: Request,
        db: Session,
        company_id: UUID,
        user_id: UUID | None,
    ) -> None:
        self.request = request
        self.db = db
        self.company_id = company_id
        self.user_id = user_id
        self.key: str | None = request.headers.get("Idempotency-Key")
        self._row: IdempotencyKey | None = None

    async def check(self, *, required: bool) -> Response | None:
        """Run the idempotency lookup. Returns a Response if this is a
        completed replay; None if the caller should proceed with the
        normal handler. Raises HTTPException for misuse / in-progress.
        """
        if not self.key:
            if required:
                raise _err(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code=CODE_KEY_REQUIRED,
                    message=(
                        "Idempotency-Key header is required for this "
                        "endpoint."
                    ),
                )
            return None

        if not is_valid_key(self.key):
            raise _err(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=CODE_KEY_INVALID,
                message=(
                    "Idempotency-Key must be 8-255 ASCII printable "
                    "characters with no whitespace."
                ),
            )

        body_bytes = await self.request.body()
        request_hash = canonical_hash(body_bytes)

        # The idempotency_keys table is global w.r.t. tenant scoping
        # (one row per (company_id, key)) — bypass the auto-scope
        # filter so we can run cross-company SELECTs in test harnesses
        # too. company_id is filtered explicitly below regardless.
        query = (
            self.db.query(IdempotencyKey)
            .execution_options(**{SCOPE_BYPASS_OPTION: True})
            .filter(
                IdempotencyKey.company_id == self.company_id,
                IdempotencyKey.key == self.key,
            )
            .with_for_update()
        )
        existing = query.first()

        if existing is None:
            row = IdempotencyKey(
                company_id=self.company_id,
                user_id=self.user_id,
                key=self.key,
                method=self.request.method,
                path=self.request.url.path,
                request_hash=request_hash,
                locked_at=_now(),
                expires_at=_now() + DEDUP_WINDOW,
            )
            self.db.add(row)
            self.db.flush()
            self._row = row
            return None

        # Method/path mismatch: client reused key on a different endpoint.
        if (
            existing.method != self.request.method
            or existing.path != self.request.url.path
        ):
            raise _err(
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_KEY_MISUSE,
                message=(
                    "Idempotency-Key was previously used on a different "
                    "endpoint."
                ),
                details={
                    "original_method": existing.method,
                    "original_path": existing.path,
                },
            )

        # In progress?
        if existing.completed_at is None:
            if (
                existing.locked_at is not None
                and (_now() - existing.locked_at) < LOCK_TIMEOUT
            ):
                raise _err(
                    status_code=status.HTTP_409_CONFLICT,
                    code=CODE_IN_PROGRESS,
                    message=(
                        "A request with this Idempotency-Key is already "
                        "in progress."
                    ),
                    headers={"Retry-After": "5"},
                )
            # Stale lock — assume previous attempt died, take over.
            existing.locked_at = _now()
            existing.request_hash = request_hash
            self.db.flush()
            self._row = existing
            return None

        # Completed: check hash.
        if existing.request_hash != request_hash:
            raise _err(
                status_code=status.HTTP_409_CONFLICT,
                code=CODE_REPLAY,
                message=(
                    "Idempotency-Key was used previously with a different "
                    "request body. Use a new key for new requests."
                ),
                details={
                    "original_request_at": existing.completed_at.isoformat()
                    if existing.completed_at
                    else None,
                    "original_path": existing.path,
                },
            )

        # Replay: return stored response verbatim.
        body_text = (
            json.dumps(existing.response_body)
            if existing.response_body is not None
            else ""
        )
        headers = dict(existing.response_headers or {})
        headers["Idempotent-Replay"] = "true"
        headers["Content-Type"] = "application/json"
        return Response(
            content=body_text,
            status_code=existing.response_status or 200,
            headers=headers,
        )

    def store_response(
        self,
        *,
        status_code: int,
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Stamp the response onto the in-flight row. No-op when no key."""
        if self._row is None:
            return
        self._row.response_status = status_code
        self._row.response_body = body
        self._row.response_headers = headers or {}
        self._row.completed_at = _now()
        self.db.flush()
