"""Stock valuation per financial year (opening/closing stock value).

TaxMind has no inventory model; Tally does. For each financial year the
company can record Tally's own opening and closing STOCK VALUE (never
quantities or items), pulled by the connector or entered manually. The
profit & loss and balance sheet fold these in (docs/PHASE_3_CLOSING_STOCK_DESIGN.md).

Values are signed in the backend convention: **Dr positive**. A net credit
(Tally shows negative stock when more was issued than received) is
negative -- it mirrors Tally and is never clamped to zero.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.money import money_column
from app.models.base import (
    Base,
    TenantScopedMixin,
    created_at_col,
    updated_at_col,
    uuid_pk,
)


class StockValuation(Base, TenantScopedMixin):
    __tablename__ = "stock_valuations"

    id: Mapped[UUID] = uuid_pk()

    # Exactly one Indian financial year: 1 April .. 31 March (enforced in the
    # service; the DB checks only ordering).
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)

    # Signed, Dr positive. May be negative (net credit). Never clamped.
    opening_value: Mapped[Decimal] = money_column()
    closing_value: Mapped[Decimal] = money_column()

    source: Mapped[str] = mapped_column(String(10), nullable=False)  # tally | manual
    # Data-quality context from the Tally pull (NULL for manual entries).
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negative_stock_items: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint(
            "company_id", "period_from", name="uq_stock_valuations_company_fy"
        ),
        CheckConstraint(
            "period_to > period_from", name="ck_stock_valuations_period_order"
        ),
        CheckConstraint(
            "source IN ('tally', 'manual')", name="ck_stock_valuations_source"
        ),
        Index("idx_stock_valuations_company", "company_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<StockValuation company={self.company_id} "
            f"{self.period_from}..{self.period_to}>"
        )
