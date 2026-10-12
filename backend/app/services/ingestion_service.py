"""Ingestion service: create (file upload) + read + list.

Extraction itself runs out-of-band via `extraction_pipeline.enqueue_extraction`
-- this service only owns the row's creation and retrieval.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import IngestionNotFound
from app.models.ingestion import Ingestion, IngestionSource

MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB, per EXTRACTION_CONTRACT.md
ALLOWED_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/heic", "application/pdf"}
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
