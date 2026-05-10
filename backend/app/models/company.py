"""Company + UserCompany models.

`Company` is the tenant root — every financial entity is scoped to one.
`UserCompany` is the membership association with a `company_role`.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

if TYPE_CHECKING:
    from app.models.user import User


# Postgres enum names match SCHEMA.sql exactly (lowercase, snake_case).
class CompanyStatus(str, PyEnum):
    active = "active"
    inactive = "inactive"
    suspended = "suspended"


class CompanyRole(str, PyEnum):
    owner = "owner"
    admin = "admin"
    accountant = "accountant"
    viewer = "viewer"


# Enum types are created once and shared across migrations.
company_status_enum = Enum(
    CompanyStatus,
    name="company_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)
company_role_enum = Enum(
    CompanyRole,
    name="company_role",
    values_callable=lambda e: [m.value for m in e],
    create_type=True,
)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    financial_year_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=text("'2026-04-01'::date"),
    )
    accounting_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'standalone'"),
    )
    status: Mapped[CompanyStatus] = mapped_column(
        company_status_enum,
        nullable=False,
        server_default=text("'active'"),
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    # ORM relationships
    members: Mapped[list[UserCompany]] = relationship(
        "UserCompany",
        back_populates="company",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("gstin", name="uq_companies_gstin"),
        CheckConstraint(
            r"gstin IS NULL OR gstin ~ "
            r"'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'",
            name="ck_companies_gstin_format",
        ),
        CheckConstraint(
            r"pan IS NULL OR pan ~ '^[A-Z]{5}[0-9]{4}[A-Z]{1}$'",
            name="ck_companies_pan_format",
        ),
        CheckConstraint(
            r"pincode IS NULL OR pincode ~ '^[0-9]{6}$'",
            name="ck_companies_pincode_format",
        ),
        CheckConstraint(
            r"state_code IS NULL OR state_code ~ '^[0-9]{2}$'",
            name="ck_companies_state_code_format",
        ),
        CheckConstraint(
            "EXTRACT(MONTH FROM financial_year_start) = 4 "
            "AND EXTRACT(DAY FROM financial_year_start) = 1",
            name="ck_companies_fy_start_april",
        ),
        CheckConstraint(
            "accounting_source IN "
            "('standalone', 'tally', 'zoho', 'quickbooks', 'busy')",
            name="ck_companies_accounting_source",
        ),
        Index(
            "idx_companies_status",
            "status",
            postgresql_where="status = 'active'",
        ),
        Index(
            "idx_companies_gstin",
            "gstin",
            postgresql_where="gstin IS NOT NULL",
        ),
    )

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"


class UserCompany(Base):
    """Membership association between a user and a company, with role.

    NOT a `TenantScopedMixin` subclass — it's a junction table whose
    own primary purpose is to scope users to companies. The CASCADE/
    RESTRICT semantics also differ from the standard tenant-scoped
    pattern.
    """

    __tablename__ = "user_companies"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[CompanyRole] = mapped_column(
        company_role_enum,
        nullable=False,
        server_default=text("'viewer'"),
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    user: Mapped[User] = relationship("User", back_populates="memberships")
    company: Mapped[Company] = relationship("Company", back_populates="members")

    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_companies_user_company"),
        Index("idx_user_companies_user", "user_id"),
        Index("idx_user_companies_company", "company_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<UserCompany user_id={self.user_id} company_id={self.company_id} "
            f"role={self.role.value}>"
        )
