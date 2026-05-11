"""Connector enrollment service (P0.23)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import NotFound, ValidationFailed
from app.core.security import create_connector_token
from app.models.connector_enrollment import ConnectorEnrollmentCode

ENROLLMENT_CODE_TTL = timedelta(minutes=15)


def _hash_code(code: str) -> str:
    """SHA-256 hex of the code. Used for storage; raw never persisted."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class _CodeNotFound(NotFound):
    code = "enrollment_code_not_found"


class _CodeExpired(ValidationFailed):
    code = "enrollment_code_expired"


class _CodeAlreadyConsumed(ValidationFailed):
    code = "enrollment_code_consumed"


class ConnectorEnrollmentService:
    """Issues + redeems one-time connector enrollment codes."""

    def __init__(self, db: Session, audit: AuditEmitter) -> None:
        self.db = db
        self.audit = audit

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def issue(  # audit-exempt: ConnectorEnrollmentCode is operational state (like idempotency_keys), not a financially significant entity per AUDIT.md
        self,
        *,
        company_id: UUID,
        created_by: UUID,
        ttl: timedelta = ENROLLMENT_CODE_TTL,
    ) -> tuple[ConnectorEnrollmentCode, str]:
        """Generate a code, persist its hash, return (row, raw_code)."""
        # 32 random bytes URL-safe-base64 → 43 chars. Plenty of entropy
        # to resist brute-force inside a 15-minute window.
        raw_code = secrets.token_urlsafe(32)
        row = ConnectorEnrollmentCode(
            company_id=company_id,
            created_by=created_by,
            code_hash=_hash_code(raw_code),
            expires_at=datetime.now(UTC) + ttl,
        )
        self.db.add(row)
        self.db.flush()
        return row, raw_code

    # ------------------------------------------------------------------
    # Enroll (consume)
    # ------------------------------------------------------------------

    def enroll(  # audit-exempt: enrollment is operational; the resulting connector activity is audited via future connector.* actions
        self, *, code: str
    ) -> tuple[UUID, UUID, str]:
        """Consume a code and issue a connector token.

        Returns (connector_id, company_id, jwt_token).
        Raises specific subclasses on miss / expired / consumed.
        """
        row = (
            self.db.query(ConnectorEnrollmentCode)
            .filter(ConnectorEnrollmentCode.code_hash == _hash_code(code))
            .first()
        )
        if row is None:
            raise _CodeNotFound("Enrollment code not found.")
        if row.consumed_at is not None:
            raise _CodeAlreadyConsumed("Enrollment code already used.")
        if row.expires_at < datetime.now(UTC):
            raise _CodeExpired("Enrollment code has expired.")

        connector_id = uuid4()
        token = create_connector_token(
            connector_id=connector_id,
            company_id=row.company_id,
        )
        row.consumed_at = datetime.now(UTC)
        self.db.flush()
        return connector_id, row.company_id, token
