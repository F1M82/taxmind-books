"""Account-deletion lifecycle (P0.45).

Three operations:

  request_deletion(db, audit, user)  — open a 30-day grace window.
  cancel_deletion(db, audit, user)   — undo while still in grace.
  process_due_deletions(db)          — worker entry point: scan
                                       grace-expired rows, hard-delete
                                       the corresponding user, emit
                                       the completion audit row.

Sole-owner check (request_deletion): a user who is the only `owner`
of any company can't be deleted without ownership transfer — DPDP
erasure must not orphan a multi-user tenant. The check uses the
same UserCompany role data the membership APIs use, so it stays in
sync with company.management.

Erasure (process_due_deletions): we *anonymise* the User row rather
than literally DELETE it.

Why anonymise instead of DELETE: the FK from `audit_logs.user_id`
is `ON DELETE SET NULL`, which fires an UPDATE on every existing
audit row that referenced this user. The `prevent_audit_modification()`
trigger refuses ALL UPDATEs on `audit_logs`, so the cascade fails
and the whole DELETE transaction rolls back — the user can never
be hard-deleted while audit history exists.

Anonymisation gives us the same DPDP outcome (the PII is gone)
without touching the audit table:

  - users.email             → 'deleted-<uuid>@deleted.invalid'
  - users.full_name         → 'Deleted user'
  - users.phone             → NULL
  - users.firm_name         → NULL
  - users.ca_membership_no  → NULL
  - users.hashed_password   → ''                # unloggable
  - users.is_active         → false
  - users.last_login_at     → NULL

Ancillary user-scoped rows that are clearly tied to the human
(memberships, push tokens, idempotency keys) are explicitly
deleted; vouchers/audit logs/companies keep their `created_by`
foreign keys (now pointing to the tombstone row) so the financial
trail survives intact.

Email confirmations on request / cancellation / completion are
listed as Phase-0 acceptance criteria but TaxMind Books has no
SMTP-or-equivalent integration yet. Each entry point calls into a
stub send_account_email() that logs the intent; Phase 1 will wire
the real provider when one is chosen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import (
    Conflict,
    NotFound,
    OwnershipTransferRequired,
)
from app.models.account_deletion_request import (
    AccountDeletionRequest,
    AccountDeletionStatus,
)
from app.models.company import CompanyRole, UserCompany
from app.models.device_token import DeviceToken
from app.models.idempotency_key import IdempotencyKey
from app.models.user import User

logger = logging.getLogger("app.services.account_lifecycle")

# 30-day cooling-off window per DPDP guidance and docs/API.md.
GRACE_PERIOD_DAYS = 30


def _send_account_email(  # audit-exempt: stub I/O, no DB mutation
    *, user_email: str, kind: str, **context: object
) -> None:
    """Email-confirmation stub.

    Phase 0 has no SMTP/SES integration; this logs the intent so the
    behaviour is observable in tests + dev and trivially mock-patchable
    when Phase 1 plugs in the real provider.
    """
    logger.info(
        "account_lifecycle.email (stub)",
        extra={"to": user_email, "kind": kind, **context},
    )


def _sole_owner_companies(  # audit-exempt: read-only membership scan
    db: Session, *, user_id: UUID
) -> list[UUID]:
    """IDs of companies where `user_id` is the only active owner.

    A multi-owner company is fine (the others can carry the load),
    and a non-owner membership obviously doesn't block deletion. We
    only flag companies where this user *is* an owner AND the count
    of distinct owners for that company is 1.
    """
    owned_company_ids = (
        db.execute(
            select(UserCompany.company_id).where(
                UserCompany.user_id == user_id,
                UserCompany.role == CompanyRole.owner,
            )
        )
        .scalars()
        .all()
    )
    if not owned_company_ids:
        return []

    # For each owned company, count distinct owners.
    blocked: list[UUID] = []
    for cid in owned_company_ids:
        other_owners = db.scalar(
            select(UserCompany.id)
            .where(
                UserCompany.company_id == cid,
                UserCompany.role == CompanyRole.owner,
                UserCompany.user_id != user_id,
            )
            .limit(1)
        )
        if other_owners is None:
            blocked.append(cid)
    return blocked


def request_deletion(
    db: Session,
    audit: AuditEmitter,
    *,
    user: User,
) -> AccountDeletionRequest:
    """Open a 30-day deletion grace period for `user`.

    Raises `Conflict('account_deletion_already_pending')` if the user
    already has an open `grace_period` request, and
    `OwnershipTransferRequired` if they are the sole owner of one or
    more companies.
    """
    existing = db.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id,
            AccountDeletionRequest.status
            == AccountDeletionStatus.grace_period,
        )
    )
    if existing is not None:
        # Idempotent-ish: return the same row rather than erroring.
        # Multiple grace_period rows for one user would be a data-shape
        # bug; the service surface preempts that here.
        return existing

    blocked = _sole_owner_companies(db, user_id=user.id)
    if blocked:
        raise OwnershipTransferRequired(
            "You are the sole owner of one or more companies. "
            "Transfer ownership or delete those companies before "
            "deleting your account.",
            details={"companies": [str(c) for c in blocked]},
        )

    now = datetime.now(UTC)
    row = AccountDeletionRequest(
        user_id=user.id,
        requested_at=now,
        grace_ends_at=now + timedelta(days=GRACE_PERIOD_DAYS),
        status=AccountDeletionStatus.grace_period,
    )
    db.add(row)
    db.flush()
    audit.emit(
        action="account.deletion_requested",
        entity_type="account_deletion_request",
        entity_id=row.id,
        old_value=None,
        new_value={
            "user_id": str(user.id),
            "grace_ends_at": row.grace_ends_at.isoformat(),
            "status": row.status.value,
        },
        actor_user_id=user.id,
    )
    _send_account_email(
        user_email=user.email,
        kind="deletion_requested",
        grace_ends_at=row.grace_ends_at.isoformat(),
    )
    return row


def cancel_deletion(
    db: Session,
    audit: AuditEmitter,
    *,
    user: User,
) -> AccountDeletionRequest:
    """Cancel `user`'s open deletion request.

    Raises `NotFound` if there is no `grace_period` row for the user.
    Re-cancelling an already-cancelled or completed row is not
    supported — the endpoint contract is "cancel the pending one".
    """
    row = db.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id,
            AccountDeletionRequest.status
            == AccountDeletionStatus.grace_period,
        )
    )
    if row is None:
        raise NotFound("No pending deletion request to cancel.")

    now = datetime.now(UTC)
    row.status = AccountDeletionStatus.cancelled
    row.cancelled_at = now
    db.flush()
    audit.emit(
        action="account.deletion_cancelled",
        entity_type="account_deletion_request",
        entity_id=row.id,
        old_value={"status": AccountDeletionStatus.grace_period.value},
        new_value={"status": row.status.value, "cancelled_at": now.isoformat()},
        actor_user_id=user.id,
    )
    _send_account_email(
        user_email=user.email,
        kind="deletion_cancelled",
    )
    return row


def find_due_requests(  # audit-exempt: read-only scan for worker
    db: Session, *, now: datetime | None = None
) -> list[UUID]:
    """Return IDs of grace-expired deletion requests, oldest first."""
    cutoff = now or datetime.now(UTC)
    return list(
        db.execute(
            select(AccountDeletionRequest.id)
            .where(
                AccountDeletionRequest.status
                == AccountDeletionStatus.grace_period,
                AccountDeletionRequest.grace_ends_at <= cutoff,
            )
            .order_by(AccountDeletionRequest.grace_ends_at.asc())
        )
        .scalars()
        .all()
    )


def _anonymized_email(user_id: UUID) -> str:
    # `.invalid` is the RFC 2606 reserved TLD; any future code that
    # tries to email this address will fail loudly rather than
    # leaking the user's real address.
    return f"deleted-{user_id}@deleted.invalid"


def process_due_deletion(
    db: Session,
    audit: AuditEmitter,
    *,
    request_id: UUID,
) -> None:
    """Process one grace-expired request: anonymise + audit.

    The user row is anonymised (not DELETE'd — see module docstring
    for the audit-trigger reason). Ancillary rows tied to the human
    user (memberships, device tokens, idempotency keys) are removed
    explicitly. The completion audit row keeps `user_id` pointing
    to the tombstone so the trail stays joinable.

    Conflict (not raised, just skipped) cases:
      - request is no longer in `grace_period` (cancelled mid-scan
        or already processed) — silently no-op.
      - user record is missing — mark request `failed`.
    """
    row = db.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.id == request_id
        )
    )
    if row is None:
        raise NotFound(f"Deletion request {request_id} not found.")

    if row.status != AccountDeletionStatus.grace_period:
        # Some other path moved it on (e.g. cancellation). Skip; the
        # worker re-scans every day.
        return

    now = datetime.now(UTC)
    row.status = AccountDeletionStatus.processing
    row.processing_started_at = now
    db.flush()

    user = db.scalar(select(User).where(User.id == row.user_id))
    if user is None:
        row.status = AccountDeletionStatus.failed
        row.failure_reason = "user record missing"
        db.flush()
        return

    user_email = user.email  # capture for the post-completion email

    audit.emit(
        action="account.deletion_completed",
        entity_type="account_deletion_request",
        entity_id=row.id,
        old_value={"status": AccountDeletionStatus.processing.value},
        new_value={
            "status": AccountDeletionStatus.completed.value,
            "completed_at": now.isoformat(),
        },
        actor_user_id=user.id,
    )

    # Drop human-only ancillary rows. We use Core DELETE so cascades
    # at the FK layer take care of any further dependents.
    db.execute(
        delete(UserCompany).where(UserCompany.user_id == user.id)
    )
    db.execute(
        delete(DeviceToken).where(DeviceToken.user_id == user.id)
    )
    db.execute(
        delete(IdempotencyKey).where(IdempotencyKey.user_id == user.id)
    )

    # Anonymise the user row. PII fields go to inert placeholders;
    # the row itself stays so audit_logs/vouchers/companies keep
    # their foreign-key integrity.
    user.email = _anonymized_email(user.id)
    user.full_name = "Deleted user"
    user.phone = None
    user.firm_name = None
    user.ca_membership_no = None
    user.is_ca = False
    user.hashed_password = ""  # impossible to satisfy → no future logins
    user.is_active = False
    user.last_login_at = None

    row.status = AccountDeletionStatus.completed
    row.completed_at = now

    _send_account_email(
        user_email=user_email,
        kind="deletion_completed",
    )


def process_due_deletions(
    db: Session,
    audit_factory,  # type: ignore[no-untyped-def]
) -> int:
    """Worker entry point: process every grace-expired request.

    `audit_factory` is a callable `() -> AuditEmitter` — each request
    gets a fresh emitter so failure to process one doesn't poison
    the next. Returns the number of successfully completed deletions.
    """
    processed = 0
    for rid in find_due_requests(db):
        try:
            process_due_deletion(db, audit_factory(), request_id=rid)
            db.commit()
            processed += 1
        except Conflict:
            db.rollback()
            logger.warning(
                "account_lifecycle.process: conflict on %s; skipping",
                rid,
            )
        except Exception as exc:
            db.rollback()
            logger.error(
                "account_lifecycle.process: %s failed: %s",
                rid,
                exc,
                exc_info=True,
            )
            # Mark the row as failed so daily re-scans don't keep
            # retrying a known-bad request.
            failed = db.scalar(
                select(AccountDeletionRequest).where(
                    AccountDeletionRequest.id == rid
                )
            )
            if failed is not None:
                failed.status = AccountDeletionStatus.failed
                failed.failure_reason = str(exc)[:1000]
                db.commit()
    return processed


__all__ = [
    "GRACE_PERIOD_DAYS",
    "cancel_deletion",
    "find_due_requests",
    "process_due_deletion",
    "process_due_deletions",
    "request_deletion",
]
