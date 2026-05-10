"""SQLAlchemy declarative base + cross-cutting column helpers.

`Base` is the single declarative base for the whole application.
`TenantScopedMixin` carries the `company_id` FK that every tenant-scoped
model uses; the dependency layer (P0.10) auto-injects a WHERE filter
for any model that inherits from it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention so Alembic emits stable constraint/index names that
# match docs/SCHEMA.sql.
#
# Note: `ck` is intentionally absent. SQLAlchemy's `%(constraint_name)s`
# token would otherwise *prefix* an already-named CheckConstraint with
# `ck_<table>_`, producing `ck_users_ck_users_email_lowercase`. Our
# CHECK constraints already carry full SCHEMA.sql-matching names
# (`ck_users_email_lowercase`, etc.), so we let those pass through
# untouched.
NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared across every model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ----------------------------------------------------------------------
# Common column helpers
# ----------------------------------------------------------------------


def uuid_pk() -> Mapped[UUID]:
    """Server-defaulted UUIDv4 primary key, matching SCHEMA.sql."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def updated_at_col() -> Mapped[datetime]:
    """Updated-at column — actual bumping happens via DB trigger.

    The `set_updated_at()` trigger function lives in the first migration
    and is attached to each table that needs it; ORM-level `onupdate`
    would race with raw SQL updates, so we let the DB own the column.
    """
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# ----------------------------------------------------------------------
# Tenant scoping
# ----------------------------------------------------------------------


class TenantScopedMixin:
    """Mixin marker for models scoped to a company.

    Carries the `company_id` FK and an index on it. The auto-scoping
    session (added in P0.10) filters on this column whenever a query
    targets a `TenantScopedMixin` subclass.

    Tables that have non-default FK semantics (e.g. CASCADE rather than
    RESTRICT, or no FK at all) declare their own `company_id` instead
    of using this mixin — `user_companies` is one such table.
    """

    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
