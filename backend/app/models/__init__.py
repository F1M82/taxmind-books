"""SQLAlchemy ORM models.

Importing this module pulls every model into `Base.metadata` so Alembic
autogenerate sees the full schema. Add new model modules to the import
list below as they land.
"""

from __future__ import annotations

from app.models.base import Base, TenantScopedMixin
from app.models.company import Company, UserCompany
from app.models.user import User

__all__ = [
    "Base",
    "Company",
    "TenantScopedMixin",
    "User",
    "UserCompany",
]
