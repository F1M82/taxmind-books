"""Daily Celery beat scan for DPDP account-deletion processing (P0.45).

Wakes up once per day, picks every grace-expired
`account_deletion_requests` row, and runs the hard-delete +
audit-log pipeline from `account_lifecycle_service`. Phase 0 wires
the task only — production scheduling (Celery beat) is configured
in deploy.

Each request gets its own AuditEmitter constructed from a
worker-source AuditContext (no Request object, no user actor;
the row's user is the implicit subject). The service handles
per-request rollback so one failure doesn't poison the batch.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from app.core.audit import AuditContext, AuditEmitter
from app.core.database import SessionLocal
from app.services import account_lifecycle_service
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers.lifecycle_tasks")


@celery_app.task(name="app.workers.process_due_account_deletions")
def process_due_account_deletions() -> int:
    """Scan for grace-expired deletion requests; hard-delete each.

    Returns the number of successfully completed deletions. Failures
    are logged and the row is flipped to `failed` so the next daily
    scan doesn't retry a known-bad request.
    """
    db = SessionLocal()
    try:
        def _audit_factory() -> AuditEmitter:
            return AuditEmitter(
                db,
                AuditContext(
                    company=None,
                    user=None,
                    ip_address=None,
                    user_agent=None,
                    request_id=uuid4(),
                    source="worker",
                ),
            )

        count = account_lifecycle_service.process_due_deletions(
            db, _audit_factory
        )
        logger.info(
            "lifecycle_tasks.process_due_account_deletions: %s "
            "deletion(s) processed",
            count,
        )
        return count
    finally:
        db.close()
