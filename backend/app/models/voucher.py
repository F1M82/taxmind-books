"""Voucher and LedgerEntry models — the financial heart of the system.

A `Voucher` is a single accounting transaction (Receipt, Payment, Sales,
Purchase, Journal, Contra, Debit Note, Credit Note). `LedgerEntry` rows
are its Dr/Cr lines. Both are tenant-scoped on `company_id`.

Soft delete only: vouchers are cancelled by setting `status='cancelled'`,
never hard-deleted (per `API.md`). Ledger entries inherit the cancellation
via the cascade on `voucher_id`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.core.money import money_column
from app.models.base import (
    Base,
    TenantScopedMixin,
    created_at_col,
    updated_at_col,
    uuid_pk,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped, mapped_column, relationship
else:
    from sqlalchemy.orm import Mapped, mapped_column, relationship


class VoucherType(str, PyEnum):
    """Tally's eight standard voucher types.

    `DebitNote` / `CreditNote` use a name without a space but the
    Postgres value retains the canonical Tally spelling with a space.
    """

    Receipt = "Receipt"
    Payment = "Payment"
    Sales = "Sales"
    Purchase = "Purchase"
    Journal = "Journal"
    Contra = "Contra"
    DebitNote = "Debit Note"
    CreditNote = "Credit Note"


class VoucherStatus(str, PyEnum):
    draft = "draft"
    pending_approval = "pending_approval"
    optional = "optional"
    # `pending_tally_post` is the initial state for a voucher that
    # exists in the books but is still waiting for Tally to confirm.
    # The dispatcher transitions it to `posted` once Tally accepts.
    # Reports treat `pending_tally_post` as a live entry; only the
    # `tally_posted_at` timestamp signals "mirrored to Tally". (P0.46d)
    pending_tally_post = "pending_tally_post"
    posted = "posted"
    cancelled = "cancelled"
    rejected_optional = "rejected_optional"


class EntryType(str, PyEnum):
    Dr = "Dr"
    Cr = "Cr"


voucher_type_enum = Enum(
    VoucherType,
    name="voucher_type",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)
voucher_status_enum = Enum(
    VoucherStatus,
    name="voucher_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)
entry_type_enum = Enum(
    EntryType,
    name="entry_type",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)


class Voucher(Base, TenantScopedMixin):
    __tablename__ = "vouchers"

    id: Mapped[UUID] = uuid_pk()

    voucher_type: Mapped[VoucherType] = mapped_column(
        voucher_type_enum, nullable=False
    )
    voucher_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_amount: Mapped[Decimal] = money_column()
    status: Mapped[VoucherStatus] = mapped_column(
        voucher_status_enum,
        nullable=False,
        server_default=text("'posted'"),
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'manual'")
    )
    source_ingestion_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    is_auto_posted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )

    # GST
    gst_applicable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    place_of_supply: Mapped[str | None] = mapped_column(String(2), nullable=True)
    cgst: Mapped[Decimal] = money_column(default="0")
    sgst: Mapped[Decimal] = money_column(default="0")
    igst: Mapped[Decimal] = money_column(default="0")
    cess: Mapped[Decimal] = money_column(default="0")

    # TDS
    tds_applicable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    tds_amount: Mapped[Decimal] = money_column(default="0")
    tds_section: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Tally posting
    tally_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tally_voucher_guid: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    tally_post_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tally_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tally_post_queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # v1.2: Optional voucher flow
    is_optional_in_tally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    approved_to_regular_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_to_regular_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    optional_rejection_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    optional_rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    optional_rejected_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    entries: Mapped[list[LedgerEntry]] = relationship(
        "LedgerEntry",
        back_populates="voucher",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # DEFERRABLE so a multi-statement insert can update voucher_number
        # after row creation in the same transaction without tripping the
        # uniqueness check mid-flight.
        UniqueConstraint(
            "company_id",
            "voucher_type",
            "voucher_number",
            name="uq_vouchers_company_number_type",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "total_amount >= 0",
            name="ck_vouchers_total_positive",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR "
            "(confidence_score >= 0 AND confidence_score <= 1)",
            name="ck_vouchers_confidence_range",
        ),
        CheckConstraint(
            "source IN ('manual', 'whatsapp', 'sms', 'email', 'photo', 'pdf', "
            "'csv', 'voice', 'tally_sync', 'recon')",
            name="ck_vouchers_source",
        ),
        CheckConstraint(
            "cgst >= 0 AND sgst >= 0 AND igst >= 0 AND cess >= 0",
            name="ck_vouchers_gst_components",
        ),
        CheckConstraint(
            "tds_amount >= 0 "
            "AND (NOT tds_applicable OR tds_section IS NOT NULL)",
            name="ck_vouchers_tds",
        ),
        CheckConstraint(
            r"place_of_supply IS NULL OR place_of_supply ~ '^[0-9]{2}$'",
            name="ck_vouchers_place_of_supply",
        ),
        Index(
            "idx_vouchers_company_date",
            "company_id",
            text("date DESC"),
        ),
        Index(
            "idx_vouchers_company_status", "company_id", "status"
        ),
        Index(
            "idx_vouchers_company_type_date",
            "company_id",
            "voucher_type",
            text("date DESC"),
        ),
        Index(
            "idx_vouchers_source_ingestion",
            "source_ingestion_id",
            postgresql_where="source_ingestion_id IS NOT NULL",
        ),
        Index(
            "idx_vouchers_unposted_to_tally",
            "company_id",
            postgresql_where="status = 'pending_tally_post'",
        ),
        Index(
            "idx_vouchers_optional_pending",
            "company_id",
            text("date DESC"),
            postgresql_where=(
                "is_optional_in_tally = TRUE "
                "AND approved_to_regular_at IS NULL "
                "AND status NOT IN ('cancelled', 'rejected_optional')"
            ),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Voucher id={self.id} type={self.voucher_type.value} "
            f"number={self.voucher_number!r} date={self.date}>"
        )


class LedgerEntry(Base, TenantScopedMixin):
    __tablename__ = "ledger_entries"

    id: Mapped[UUID] = uuid_pk()

    voucher_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vouchers.id", ondelete="CASCADE"),
        nullable=False,
    )
    ledger_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ledgers.id", ondelete="RESTRICT"),
        nullable=False,
    )

    amount: Mapped[Decimal] = money_column()
    entry_type: Mapped[EntryType] = mapped_column(entry_type_enum, nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)

    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cgst: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    sgst: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    igst: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    tds_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    tds_section: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = created_at_col()

    voucher: Mapped[Voucher] = relationship("Voucher", back_populates="entries")

    __table_args__ = (
        UniqueConstraint(
            "voucher_id", "line_number", name="uq_ledger_entries_voucher_line"
        ),
        CheckConstraint(
            "amount > 0", name="ck_ledger_entries_amount_positive"
        ),
        CheckConstraint(
            "gst_rate IS NULL OR (gst_rate >= 0 AND gst_rate <= 100)",
            name="ck_ledger_entries_gst_rate",
        ),
        Index("idx_ledger_entries_voucher", "voucher_id"),
        Index("idx_ledger_entries_ledger", "ledger_id"),
        Index(
            "idx_ledger_entries_company_ledger",
            "company_id",
            "ledger_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<LedgerEntry id={self.id} voucher={self.voucher_id} "
            f"ledger={self.ledger_id} {self.entry_type.value} {self.amount}>"
        )
