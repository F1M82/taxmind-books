"""Domain exception hierarchy.

Per `docs/API.md` §"Standard error envelope": every error response is::

    {
      "error": {
        "code": "<stable_code>",
        "message": "<user-displayable>",
        "details": { ... optional ... }
      },
      "request_id": "<uuid>"
    }

Service / route code raises a `DomainException` (or one of its
subclasses); the handler in `app.api.errors` translates it into the
envelope. The `code` field is a v1 contract — clients switch on it.

Adding a new error code: add it to API.md §"Error code reference"
*first*, then add a subclass here that hardcodes the code/status.
"""

from __future__ import annotations

from typing import Any


class DomainException(Exception):
    """Base for every error that should surface as a JSON envelope.

    Subclasses set `status_code` and `code` as class attributes; the
    constructor takes the user-facing message (and optional details).
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_envelope(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}


# ---------------------------------------------------------------------
# 4xx — client errors
# ---------------------------------------------------------------------


class BadRequest(DomainException):
    status_code = 400
    code = "bad_request"


class Unauthorized(DomainException):
    status_code = 401
    code = "invalid_credentials"


class Forbidden(DomainException):
    status_code = 403
    code = "forbidden"


class NotFound(DomainException):
    status_code = 404
    code = "not_found"


class Conflict(DomainException):
    status_code = 409
    code = "conflict"


class ValidationFailed(DomainException):
    """Domain-level validation that failed business rules.

    Distinct from `RequestValidationError` from Pydantic / FastAPI —
    those map to the same `validation_error` code via the handler in
    `app.api.errors` but originate at the request-decoding layer.
    """

    status_code = 422
    code = "validation_error"


class FileTooLarge(DomainException):
    status_code = 413
    code = "file_too_large"


class UnsupportedMediaType(DomainException):
    status_code = 415
    code = "unsupported_media_type"


class RateLimitExceeded(DomainException):
    status_code = 429
    code = "rate_limit_exceeded"


# ---------------------------------------------------------------------
# 5xx — upstream / availability
# ---------------------------------------------------------------------


class Unavailable(DomainException):
    status_code = 503
    code = "service_unavailable"


class UpstreamError(DomainException):
    status_code = 502
    code = "upstream_error"


# ---------------------------------------------------------------------
# Domain-specific subclasses tied to API.md error codes.
# Add new ones here when adding to API.md §"Error code reference".
# ---------------------------------------------------------------------


class InvalidCredentials(Unauthorized):
    code = "invalid_credentials"


class UserInactive(Forbidden):
    code = "user_inactive"


class InsufficientRole(Forbidden):
    code = "insufficient_role"


class CompanyNotFound(NotFound):
    code = "company_not_found"


class VoucherNotFound(NotFound):
    code = "voucher_not_found"


class LedgerNotFound(NotFound):
    code = "ledger_not_found"


class EmailAlreadyRegistered(Conflict):
    code = "email_already_registered"


class GstinAlreadyRegistered(Conflict):
    code = "gstin_already_registered"


class VoucherImmutableField(Conflict):
    code = "voucher_immutable_field"


class VoucherAlreadyCancelled(Conflict):
    code = "voucher_already_cancelled"


class VoucherNumberCollision(Conflict):
    code = "voucher_number_collision"


class VoucherEntriesUnbalanced(ValidationFailed):
    code = "voucher_entries_unbalanced"


class VoucherTypeRuleViolation(ValidationFailed):
    code = "voucher_type_rule_violation"


class VoucherNotOptional(Conflict):
    code = "voucher_not_optional"


class VoucherRejected(Conflict):
    code = "voucher_rejected"


class LedgerInUse(Conflict):
    code = "ledger_in_use"


class UserNotFound(NotFound):
    code = "user_not_found"


class AlreadyMember(Conflict):
    code = "already_member"


class ConnectorOffline(Unavailable):
    code = "connector_offline"


class OwnershipTransferRequired(Conflict):
    code = "ownership_transfer_required"
