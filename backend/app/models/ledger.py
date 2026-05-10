"""Ledger model — chart-of-accounts row, mirror of a Tally ledger.

Tenant-scoped. One row per ledger master per company. Synced from Tally
on connector handshake; updated when Tally sync delivers changes. Soft
delete only via `is_active=False` — no hard delete (per `API.md`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
    from sqlalchemy.orm import Mapped, mapped_column
else:
    from sqlalchemy.orm import Mapped, mapped_column


class BalanceType(str, PyEnum):
    Dr = "Dr"
    Cr = "Cr"


# Shared enum type — created once via `balance_type` Postgres type.
balance_type_enum = Enum(
    BalanceType,
    name="balance_type",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)


class Ledger(Base, TenantScopedMixin):
    __tablename__ = "ledgers"

    id: Mapped[UUID] = uuid_pk()

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parent_ledger_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ledgers.id", ondelete="SET NULL"),
        nullable=True,
    )

    opening_balance: Mapped[Decimal] = money_column(default="0")
    balance_type: Mapped[BalanceType] = mapped_column(
        balance_type_enum,
        nullable=False,
        server_default=text("'Dr'"),
    )

    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(15), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )

    tally_master_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tally_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint(
            "company_id", "name", name="uq_ledgers_company_name"
        ),
        UniqueConstraint(
            "company_id", "tally_master_id", name="uq_ledgers_company_tally"
        ),
        CheckConstraint(
            r"gstin IS NULL OR gstin ~ "
            r"'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="ck_ledgers_gstin_format",
        ),
        CheckConstraint(
            r"pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'",
            name="ck_ledgers_pan_format",
        ),
        Index("idx_ledgers_company", "company_id"),
        Index("idx_ledgers_company_active", "company_id", "is_active"),
        Index("idx_ledgers_company_group", "company_id", "group_name"),
        Index(
            "idx_ledgers_gstin",
            "company_id",
            "gstin",
            postgresql_where="gstin IS NOT NULL",
        ),
        Index(
            "idx_ledgers_pan",
            "company_id",
            "pan",
            postgresql_where="pan IS NOT NULL",
        ),
        # gin trigram index used by the fuzzy /ledgers?q= search in P0.17.
        Index(
            "idx_ledgers_name_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Ledger id={self.id} name={self.name!r}>"
