"""User model — the global authentication identity.

Users are NOT tenant-scoped. Membership in a company is recorded in
`user_companies`. A user may belong to multiple companies (typical for
CAs and for owners with related entities).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk

if TYPE_CHECKING:
    from app.models.company import Company, UserCompany


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(15), nullable=True)

    is_ca: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    firm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ca_membership_no: Mapped[str | None] = mapped_column(String(50), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    # ORM relationships
    companies: Mapped[list[Company]] = relationship(
        "Company",
        secondary="user_companies",
        viewonly=True,
    )
    memberships: Mapped[list[UserCompany]] = relationship(
        "UserCompany",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = LOWER(email)", name="ck_users_email_lowercase"),
        CheckConstraint(
            r"phone IS NULL OR phone ~ '^[+]?[0-9]{10,15}$'",
            name="ck_users_phone_format",
        ),
        Index("idx_users_email", "email"),
        # Partial index: only active users
        Index(
            "idx_users_active",
            "is_active",
            postgresql_where="is_active = TRUE",
        ),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
