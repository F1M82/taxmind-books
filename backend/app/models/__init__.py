"""SQLAlchemy ORM models.

Importing this module pulls every model into `Base.metadata` so Alembic
autogenerate sees the full schema. Add new model modules to the import
list below as they land.
"""

from __future__ import annotations

from app.models.account_deletion_request import (
    AccountDeletionRequest,
    AccountDeletionStatus,
)
from app.models.audit_log import AuditLog
from app.models.base import Base, TenantScopedMixin
from app.models.company import Company, UserCompany
from app.models.connector_enrollment import ConnectorEnrollmentCode
from app.models.device_token import DevicePlatform, DeviceToken
from app.models.idempotency_key import IdempotencyKey
from app.models.ledger import BalanceType, Ledger
from app.models.user import User
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)

__all__ = [
    "AccountDeletionRequest",
    "AccountDeletionStatus",
    "AuditLog",
    "BalanceType",
    "Base",
    "Company",
    "ConnectorEnrollmentCode",
    "DevicePlatform",
    "DeviceToken",
    "EntryType",
    "IdempotencyKey",
    "Ledger",
    "LedgerEntry",
    "TenantScopedMixin",
    "User",
    "UserCompany",
    "Voucher",
    "VoucherStatus",
    "VoucherType",
]
