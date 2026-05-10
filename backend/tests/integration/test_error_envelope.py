"""Tests for the standard error envelope from `docs/API.md`.

Exercises every error-handler branch via a handful of probe routes
attached to a fresh test app:

  * DomainException subclass    → status, code, message echoed
  * Pydantic RequestValidation  → 422 with validation_error envelope
  * HTTPException (envelope-shaped detail) → passes through verbatim
  * HTTPException (string detail)         → wrapped in envelope
  * Unhandled Exception                   → 500 internal_error, no leak
  * X-Request-ID echo + auto-generation
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.api.errors import REQUEST_ID_HEADER, install_error_handlers
from app.core.exceptions import (
    Conflict,
    DomainException,
    EmailAlreadyRegistered,
    NotFound,
    VoucherEntriesUnbalanced,
)
from app.main import create_app
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel


class EchoBody(BaseModel):
    amount: int
    name: str


def _build_error_app() -> FastAPI:
    """A standalone app with probe routes covering each error type."""
    # `create_app()` installs handlers once; install_error_handlers is
    # idempotent for the test app's ergonomics.
    app = create_app()
    install_error_handlers(app)

    @app.post("/_err/echo")
    def echo(body: EchoBody) -> dict[str, int | str]:
        return {"amount": body.amount, "name": body.name}

    @app.get("/_err/not-found")
    def not_found() -> None:
        raise NotFound("Voucher not found.")

    @app.get("/_err/voucher-not-found")
    def voucher_not_found() -> None:
        from app.core.exceptions import VoucherNotFound

        raise VoucherNotFound("That voucher does not exist.")

    @app.get("/_err/conflict")
    def conflict() -> None:
        raise EmailAlreadyRegistered(
            "Email already registered.",
            details={"field": "email"},
        )

    @app.get("/_err/validation")
    def validation() -> None:
        raise VoucherEntriesUnbalanced(
            "Dr total must equal Cr total.",
            details={"dr": "100.00", "cr": "90.00"},
        )

    @app.get("/_err/http-string")
    def http_string() -> None:
        raise HTTPException(status_code=403, detail="Insufficient role")

    @app.get("/_err/http-envelope")
    def http_envelope() -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "voucher_number_collision",
                    "message": "Voucher number already used.",
                    "details": {"voucher_number": "RV/2026/001"},
                }
            },
        )

    @app.get("/_err/boom")
    def boom() -> None:
        raise RuntimeError("internal: should never leak")

    @app.get("/_err/dom-extra")
    def dom_extra() -> None:
        raise Conflict(
            "Generic conflict.", details={"hint": "retry-later"}
        )

    return app


# Module-level client so the suite reuses a single app instance.
# `raise_server_exceptions=False` lets us assert the 500-handler envelope
# rather than having TestClient re-raise the original exception.
_client = TestClient(_build_error_app(), raise_server_exceptions=False)


# ---------------- DomainException family ----------------


def test_domain_not_found_404() -> None:
    r = _client.get("/_err/not-found")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Voucher not found."
    assert UUID(body["request_id"])


def test_domain_named_subclass_uses_specific_code() -> None:
    r = _client.get("/_err/voucher-not-found")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "voucher_not_found"


def test_domain_conflict_with_details() -> None:
    r = _client.get("/_err/conflict")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "email_already_registered"
    assert body["error"]["details"] == {"field": "email"}


def test_domain_validation_failed_422() -> None:
    r = _client.get("/_err/validation")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "voucher_entries_unbalanced"


def test_generic_conflict_envelope() -> None:
    r = _client.get("/_err/dom-extra")
    body = r.json()
    assert r.status_code == 409
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"] == {"hint": "retry-later"}


# ---------------- Pydantic validation ----------------


def test_pydantic_validation_error_envelope() -> None:
    """Missing required body field → 422 with validation_error envelope."""
    r = _client.post("/_err/echo", json={"amount": 100})  # name missing
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request body failed validation."
    errors = body["error"]["details"]["errors"]
    assert errors, body
    # Find the error pointing at the missing `name` field.
    assert any("name" in e["loc"] for e in errors), errors
    # `input` field should NOT be echoed back (might contain user data).
    assert all("input" not in e for e in errors)


def test_pydantic_validation_wrong_type() -> None:
    r = _client.post(
        "/_err/echo", json={"amount": "not-a-number", "name": "ok"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


# ---------------- HTTPException ----------------


def test_http_exception_string_detail_wrapped() -> None:
    r = _client.get("/_err/http-string")
    assert r.status_code == 403
    body = r.json()
    # 403 default code is `forbidden`; message comes from detail string.
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == "Insufficient role"
    assert UUID(body["request_id"])


def test_http_exception_envelope_passes_through() -> None:
    r = _client.get("/_err/http-envelope")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "voucher_number_collision"
    assert body["error"]["message"] == "Voucher number already used."
    assert body["error"]["details"]["voucher_number"] == "RV/2026/001"
    # request_id is added even when the inner detail didn't have one.
    assert UUID(body["request_id"])


# ---------------- 500 (no stack-trace leak) ----------------


def test_unhandled_exception_returns_generic_500() -> None:
    r = _client.get("/_err/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    # The original exception text must NOT leak.
    assert "should never leak" not in body["error"]["message"]
    assert "RuntimeError" not in body["error"]["message"]
    assert UUID(body["request_id"])


# ---------------- X-Request-ID ----------------


def test_request_id_is_echoed_when_provided() -> None:
    rid = str(uuid4())
    r = _client.get(
        "/_err/not-found", headers={REQUEST_ID_HEADER: rid}
    )
    assert r.headers[REQUEST_ID_HEADER] == rid
    assert r.json()["request_id"] == rid


def test_request_id_is_generated_when_absent() -> None:
    r = _client.get("/_err/not-found")
    rid = r.headers[REQUEST_ID_HEADER]
    UUID(rid)  # parses successfully
    assert r.json()["request_id"] == rid


def test_request_id_malformed_header_replaced_silently() -> None:
    r = _client.get(
        "/_err/not-found", headers={REQUEST_ID_HEADER: "not-a-uuid"}
    )
    assert r.status_code == 404
    rid = r.headers[REQUEST_ID_HEADER]
    UUID(rid)  # parses


# ---------------- DomainException base ----------------


def test_domain_exception_as_envelope_helper() -> None:
    """Sanity check: as_envelope() returns the right shape."""
    exc = DomainException(
        "Boom.", details={"hint": "retry-later"}
    )
    env = exc.as_envelope()
    assert env == {
        "error": {
            "code": "internal_error",
            "message": "Boom.",
            "details": {"hint": "retry-later"},
        }
    }
