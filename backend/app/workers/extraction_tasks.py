"""Celery task for invoice extraction.

Dormant in Phase 0 (CELERY_TASK_ALWAYS_EAGER=1 routes every ingestion
through `_enqueue_in_process` in `extraction_pipeline`, not this task)
-- same trade-off as `app.workers.posting_tasks`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from celery.exceptions import MaxRetriesExceededError

from app.core.database import SessionLocal
from app.models.ingestion import Ingestion, IngestionStatus
from app.services.extraction import extraction_pipeline
from app.services.extraction.invoice_extractor import ExtractionHardFailure
from app.workers.celery_app import celery_app

# Rule "Hard failure" in docs/EXTRACTION_CONTRACT.md: 30s, 1m, 2m, 5m, 15m.
_HARD_FAILURE_BACKOFF_SECONDS = [30, 60, 120, 300, 900]


@celery_app.task(
    bind=True,
    name="app.workers.extract_invoice",
    max_retries=len(_HARD_FAILURE_BACKOFF_SECONDS),
)
def extract_invoice_task(self, ingestion_id: str, company_id: str):  # type: ignore[no-untyped-def]
    db = SessionLocal()
    try:
        extraction_pipeline.run_extraction(
            db, ingestion_id=UUID(ingestion_id), company_id=UUID(company_id)
        )
        db.commit()
    except ExtractionHardFailure as exc:
        db.rollback()
        countdown = _HARD_FAILURE_BACKOFF_SECONDS[
            min(self.request.retries, len(_HARD_FAILURE_BACKOFF_SECONDS) - 1)
        ]
        try:
            raise self.retry(exc=exc, countdown=countdown)
        except MaxRetriesExceededError:
            db2 = SessionLocal()
            try:
                ingestion = (
                    db2.query(Ingestion)
                    .filter(
                        Ingestion.id == UUID(ingestion_id),
                        Ingestion.company_id == UUID(company_id),
                    )
                    .first()
                )
                if ingestion is not None:
                    ingestion.status = IngestionStatus.failed
                    ingestion.failure_reason = "extraction_unavailable"
                    ingestion.failed_at = datetime.now(UTC)
                    db2.commit()
            finally:
                db2.close()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
