"""Ingestion service: create (file upload) + read + list.

Extraction itself runs out-of-band via `extraction_pipeline.enqueue_extraction`
-- this service only owns the row's creation and retrieval.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import IngestionAlreadyResolved, IngestionNotFound
from app.models.ingestion import Ingestion, IngestionSource, IngestionStatus

MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB, per EXTRACTION_CONTRACT.md
ALLOWED_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/heic", "application/pdf"}
)

# Statuses from which a human can manually resolve an ingestion (the
# mobile review queue). `posted`/`discarded` are terminal; `received`/
# `extracting`/`extracted`/`matching` are mid-pipeline and not shown in
# the queue, but are excluded here too in case of a slow/stuck row.
RESOLVABLE_STATUSES = frozenset(
    {IngestionStatus.review, IngestionStatus.matched, IngestionStatus.failed}
)


def _snapshot(ing: Ingestion) -> dict[str, Any]:
    return {
        "id": str(ing.id),
        "company_id": str(ing.company_id),
        "source": ing.source.value,
        "status": ing.status.value,
        "content_type": ing.content_type,
        "content_size": ing.content_size,
    }


class IngestionService:
    def __init__(
        self, db: Session, audit: AuditEmitter, company_id: UUID
    ) -> None:
        self.db = db
        self.audit = audit
        self.company_id = company_id

    def create(
        self,
        *,
        source: str,
        content_type: str,
        content: bytes,
        idempotency_key: str | None,
        created_by: UUID | None,
    ) -> Ingestion:
        from app.services.extraction import storage

        ingestion_id = uuid4()
        ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/heic": "heic",
            "application/pdf": "pdf",
        }.get(content_type, "bin")
        key = f"{self.company_id}/{ingestion_id}/original.{ext}"
        storage.save_bytes(key, content)

        ingestion = Ingestion(
            id=ingestion_id,
            company_id=self.company_id,
            source=IngestionSource(source),
            s3_object_key=key,
            content_type=content_type,
            content_size=len(content),
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
        self.db.add(ingestion)
        self.db.flush()

        self.audit.emit(
            action="ingestion.created",
            entity_type="ingestion",
            entity_id=ingestion.id,
            old_value=None,
            new_value=_snapshot(ingestion),
        )
        return ingestion

    def get_by_idempotency_key(self, key: str) -> Ingestion | None:
        return (
            self.db.query(Ingestion)
            .filter(
                Ingestion.company_id == self.company_id,
                Ingestion.idempotency_key == key,
            )
            .first()
        )

    def get(self, ingestion_id: UUID) -> Ingestion:
        ingestion = (
            self.db.query(Ingestion)
            .filter(
                Ingestion.id == ingestion_id,
                Ingestion.company_id == self.company_id,
            )
            .first()
        )
        if ingestion is None:
            raise IngestionNotFound(f"ingestion {ingestion_id} not found")
        return ingestion

    def list(
        self, *, status_filter: str | None, limit: int
    ) -> tuple[list[Ingestion], int]:
        query = self.db.query(Ingestion).filter(
            Ingestion.company_id == self.company_id
        )
        if status_filter is not None:
            query = query.filter(Ingestion.status == status_filter)
        query = query.order_by(Ingestion.received_at.desc())
        total = query.count()
        rows = query.limit(limit).all()
        return rows, total

    def discard(self, ingestion_id: UUID, *, reason: str | None) -> Ingestion:
        """Manually dismiss a stuck ingestion (duplicate, junk scan, etc.)."""
        ingestion = self.get(ingestion_id)
        if ingestion.status not in RESOLVABLE_STATUSES:
            raise IngestionAlreadyResolved(
                f"Ingestion is already {ingestion.status.value}; "
                "nothing to discard."
            )
        before = _snapshot(ingestion)
        ingestion.status = IngestionStatus.discarded
        self.db.flush()
        self.audit.emit(
            action="ingestion.discarded",
            entity_type="ingestion",
            entity_id=ingestion.id,
            old_value=before,
            new_value=_snapshot(ingestion) | {"reason": reason},
        )
        return ingestion

    def link_voucher(self, ingestion_id: UUID, *, voucher_id: UUID) -> Ingestion:
        """Record that a human manually created `voucher_id` for this
        ingestion (the extraction pipeline couldn't auto-post it). The
        caller is responsible for confirming the voucher exists and
        belongs to this company before calling this.
        """
        ingestion = self.get(ingestion_id)
        if ingestion.status not in RESOLVABLE_STATUSES:
            raise IngestionAlreadyResolved(
                f"Ingestion is already {ingestion.status.value}; "
                "cannot link a voucher."
            )
        before = _snapshot(ingestion)
        ingestion.status = IngestionStatus.posted
        ingestion.posted_voucher_id = voucher_id
        self.db.flush()
        self.audit.emit(
            action="ingestion.voucher_linked",
            entity_type="ingestion",
            entity_id=ingestion.id,
            old_value=before,
            new_value=_snapshot(ingestion),
        )
        return ingestion
