"""Ingestion model — a raw inbound capture (invoice photo/PDF today;
WhatsApp/SMS/email in a later phase), before and through extraction.

Tenant-scoped. Flow B (v1.2): there is no separate `draft_vouchers`
table — an AI-extracted entry is a `Voucher` directly. This model only
carries the raw file + the extraction pipeline's working state.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    TenantScopedMixin,
    created_at_col,
    updated_at_col,
    uuid_pk,
)


class IngestionSource(str, PyEnum):
    whatsapp = "whatsapp"
    sms = "sms"
    email = "email"
    photo = "photo"
    pdf = "pdf"
    csv = "csv"
    voice = "voice"
    manual = "manual"
    tally_sync = "tally_sync"


class IngestionStatus(str, PyEnum):
    received = "received"
    extracting = "extracting"
    extracted = "extracted"
    matching = "matching"
    matched = "matched"
    review = "review"
    posted = "posted"
    failed = "failed"
    discarded = "discarded"


ingestion_source_enum = Enum(
    IngestionSource,
    name="ingestion_source",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)
ingestion_status_enum = Enum(
    IngestionStatus,
    name="ingestion_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)


class Ingestion(Base, TenantScopedMixin):
    __tablename__ = "ingestions"

    id: Mapped[UUID] = uuid_pk()

    source: Mapped[IngestionSource] = mapped_column(
        ingestion_source_enum, nullable=False
    )
    status: Mapped[IngestionStatus] = mapped_column(
        ingestion_status_enum,
        nullable=False,
        server_default=text("'received'"),
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Storage key for the uploaded file, resolved by
    # app.services.extraction.storage (local disk today; named
    # s3_object_key to match docs/SCHEMA.sql's authoritative column name
    # and keep a future S3 swap a storage-layer-only change).
    s3_object_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    content_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    content_size: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    sender_identifier: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    received_at: Mapped[datetime] = created_at_col()
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    posted_voucher_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="SET NULL"),
        nullable=True,
    )
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    extraction_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    extraction_flags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::JSONB")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "idempotency_key",
            name="uq_ingestions_company_idempotency",
        ),
        CheckConstraint(
            "content_size IS NULL OR content_size > 0",
            name="ck_ingestions_size_positive",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_ingestions_extraction_confidence",
        ),
        Index("idx_ingestions_company_status", "company_id", "status"),
        Index(
            "idx_ingestions_company_received",
            "company_id",
            text("received_at DESC"),
        ),
        Index(
            "idx_ingestions_pending",
            "status",
            postgresql_where=text(
                "status IN ('received', 'extracting', 'matching')"
            ),
        ),
    )
