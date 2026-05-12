"""device_tokens table (v1.2 — P0.44)

Per-user push notification targets. Each row pairs a user with an
FCM/APNs/web-push token; the API layer reactivates an existing row
on re-registration rather than creating duplicates (the unique
constraint on `token` would block that anyway, but we want a
predictable response shape).

The `device_platform` enum lives only here; SCHEMA.sql declares it
near the device_tokens table itself, so no separate enum migration
preceded this one.

See docs/SCHEMA.sql §"device_tokens" and docs/API.md §"Devices &
Push Notifications".

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE device_platform AS ENUM ('android', 'ios', 'web')")

    op.create_table(
        "device_tokens",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(500), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "android",
                "ios",
                "web",
                name="device_platform",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("app_version", sa.String(50), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index(
        "idx_device_tokens_user_active",
        "device_tokens",
        ["user_id", "is_active"],
    )

    # updated_at trigger — the set_updated_at() function ships in 0001.
    op.execute(
        "CREATE TRIGGER trg_device_tokens_updated_at "
        "BEFORE UPDATE ON device_tokens "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_device_tokens_updated_at ON device_tokens"
    )
    op.drop_index("idx_device_tokens_user_active", table_name="device_tokens")
    op.drop_table("device_tokens")
    op.execute("DROP TYPE device_platform")
