"""ConnectorEnrollmentCode — one-time codes used to enroll a Tally connector.

The connector enrollment ceremony from CONNECTOR_PROTOCOL.md needs a
short-lived single-use shared secret. The user (owner of a company)
calls `POST /connector/enrollment-codes` from the mobile app; the
backend stores SHA-256(code) + 15-minute expiry, and returns the raw
code in the response. The connector running on the user's PC then
calls `POST /connector/enroll` with the code to receive a long-lived
connector token (1-year JWT).

Each row carries `company_id` so the issued connector token is bound
to one company, per the protocol's tenant guarantees.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_col, uuid_pk


class ConnectorEnrollmentCode(Base):
    __tablename__ = "connector_enrollment_codes"

    id: Mapped[UUID] = uuid_pk()

    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = created_at_col()

    __table_args__ = (
        UniqueConstraint(
            "code_hash", name="uq_connector_enrollment_codes_hash"
        ),
        Index(
            "idx_connector_enrollment_codes_company",
            "company_id",
            "created_at",
        ),
        Index(
            "idx_connector_enrollment_codes_pending",
            "expires_at",
            postgresql_where="consumed_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ConnectorEnrollmentCode id={self.id} "
            f"company={self.company_id} consumed={self.consumed_at}>"
        )
