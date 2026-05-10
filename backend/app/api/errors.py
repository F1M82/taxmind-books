"""FastAPI exception handlers — turn errors into the API.md envelope.

Every error response on the wire has the shape::

    {
      "error": {"code": "<stable>", "message": "...", "details": {...}},
      "request_id": "<uuid>"
    }

The `request_id` echoes the inbound `X-Request-ID` header when present,
or is generated server-side. The middleware also stamps `X-Request-ID`
on the response for clients to correlate with their logs.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import DomainException

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_ATTR = "request_id"

logger = logging.getLogger("app.api.errors")


# ---------------------------------------------------------------------
# Request-id middleware
# ---------------------------------------------------------------------


class RequestIDMiddleware:
    """Pure ASGI middleware: resolve / generate `request_id` per request,
    stash it on the ASGI scope so every Request derived from the scope
    sees it (including the one passed to exception handlers), and stamp
    `X-Request-ID` on the outbound response.

    A pure ASGI middleware is used (not Starlette's BaseHTTPMiddleware)
    because BaseHTTPMiddleware does not reliably share `request.state`
    across the exception-handler boundary.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _resolve_request_id_from_scope(scope)
        # Stash on the scope so any Request(scope, ...) created later
        # (including by FastAPI's exception handlers) sees it.
        scope["state"] = scope.get("state") or {}
        scope["state"][REQUEST_ID_ATTR] = rid

        rid_header = (REQUEST_ID_HEADER.lower().encode("ascii"), str(rid).encode("ascii"))

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Replace any duplicate request-id header from upstream.
                headers = [
                    (k, v)
                    for k, v in headers
                    if k.lower() != REQUEST_ID_HEADER.lower().encode("ascii")
                ]
                headers.append(rid_header)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_header)


def _resolve_request_id_from_scope(scope: Scope) -> UUID:
    for k, v in scope.get("headers", []):
        if k.lower() == REQUEST_ID_HEADER.lower().encode("ascii"):
            try:
                return UUID(v.decode("ascii"))
            except (ValueError, UnicodeDecodeError):
                break
    return uuid4()


def _request_id(request: Request) -> str:
    state = request.scope.get("state") or {}
    rid = state.get(REQUEST_ID_ATTR)
    if rid is None:
        rid = uuid4()
    return str(rid)


def _envelope(
    *,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"error": err, "request_id": request_id}


# ---------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------


async def _domain_exception_handler(
    request: Request, exc: DomainException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code=exc.code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details,
        ),
    )


async def _request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic / FastAPI body validation → `validation_error` envelope."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            code="validation_error",
            message="Request body failed validation.",
            request_id=_request_id(request),
            details={"errors": _serialize_validation_errors(exc.errors())},
        ),
    )


def _serialize_validation_errors(
    errors: list[Any],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for err in errors:
        item = {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", "value_error"),
        }
        # Pydantic v2 attaches the offending input under `input`; that
        # may contain raw user data we don't want to echo. Drop it.
        serialized.append(item)
    return serialized


async def _http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Translate FastAPI HTTPException to the envelope.

    If `exc.detail` is already an envelope-shaped dict (`{"error": {…}}`)
    we pass it through verbatim. Otherwise we wrap a generic message
    using a status-code-derived code.
    """
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        body = dict(detail)
        body["request_id"] = _request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=exc.headers,
        )

    code = _status_to_default_code(exc.status_code)
    message = (
        detail
        if isinstance(detail, str)
        else _default_message(exc.status_code)
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code=code,
            message=message,
            request_id=_request_id(request),
        ),
        headers=exc.headers,
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Last-resort handler: never leak stack traces or framework internals."""
    rid = _request_id(request)
    logger.exception("unhandled exception", extra={"request_id": rid})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            code="internal_error",
            message="An unexpected error occurred.",
            request_id=rid,
        ),
    )


def _status_to_default_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "invalid_credentials",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "file_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limit_exceeded",
        500: "internal_error",
        502: "upstream_error",
        503: "service_unavailable",
    }.get(status_code, "error")


def _default_message(status_code: int) -> str:
    return {
        400: "Bad request.",
        401: "Authentication required.",
        403: "Forbidden.",
        404: "Resource not found.",
        405: "Method not allowed.",
        409: "Conflict.",
        413: "Request entity too large.",
        415: "Unsupported media type.",
        422: "Request validation failed.",
        429: "Too many requests.",
        500: "Internal server error.",
        502: "Upstream error.",
        503: "Service unavailable.",
    }.get(status_code, "Error.")


# ---------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------


_INSTALLED_MARKER = "_taxmind_error_handlers_installed"


def install_error_handlers(app: FastAPI) -> None:
    """Register error handlers + the request-id middleware (idempotent).

    Calling twice is a no-op — `add_middleware` is not itself
    idempotent and re-registering RequestIDMiddleware would generate
    competing request IDs at each stack layer.
    """
    if getattr(app.state, _INSTALLED_MARKER, False):
        return
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(DomainException, _domain_exception_handler)
    app.add_exception_handler(
        RequestValidationError, _request_validation_handler
    )
    app.add_exception_handler(HTTPException, _http_exception_handler)
    # Catch-all: install AFTER the specific handlers so the framework
    # tries them first.
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    app.state._taxmind_error_handlers_installed = True
