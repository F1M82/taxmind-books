"""DeviceToken — push-notification target for a user's installed app.

Each row is one (user, token) pairing produced by the mobile or web
client when it registers for notifications. The actual fan-out to
FCM/APNs lives in `app/services/notification_service.py`; this model
is the source of truth for "which targets does this user have."

Re-registering the same token is idempotent: the API layer flips
`is_active=true` and refreshes `last_active_at` rather than creating
a duplicate row. The `uq_device_tokens_token` unique constraint
enforces that at the DB level so a misbehaving client can't shard a
single token across multiple users.

`is_active=false` rather than a hard delete on unregister lets
notification flows reason about historical targets if needed (e.g.
to suppress duplicate notifications across re-registrations).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, updated_at_col, uuid_pk


class DevicePlatform(str, PyEnum):
    android = "android"
    ios = "ios"
    web = "web"


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[UUID] = uuid_pk()

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    token: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[DevicePlatform] = mapped_column(
        SAEnum(
            DevicePlatform,
            name="device_platform",
            values_callable=lambda enum: [e.value for e in enum],
            native_enum=True,
        ),
        nullable=False,
    )
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
        Index("idx_device_tokens_user_active", "user_id", "is_active"),
    )

    def __repr__(self) -> str:
        return (
            f"<DeviceToken id={self.id} user={self.user_id} "
            f"platform={self.platform.value} active={self.is_active}>"
        )
