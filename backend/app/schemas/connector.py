"""Connector enrollment + status schemas (P0.23)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.schemas.common import TaxMindBooksBase


class EnrollmentCodeOut(TaxMindBooksBase):
    """Response from POST /api/v1/connector/enrollment-codes.

    The raw `code` is returned exactly once; only its hash is stored.
    """

    code: str
    expires_at: datetime
    company_id: UUID


class EnrollRequest(TaxMindBooksBase):
    code: str


class EnrollResponse(TaxMindBooksBase):
    connector_id: UUID
    company_id: UUID
    connector_token: str
    expires_in_days: int


class ConnectorStatusOut(TaxMindBooksBase):
    """`GET /connector/status` response per API.md.

    Connected: operational fields are populated.
    Disconnected: only company_id + connected (+ last_seen_at if any
    prior connection persisted; Phase 0 has no DB-backed history,
    so disconnected = nulls).
    """

    company_id: UUID
    connected: bool
    last_seen_at: datetime | None = None
    tally_running: bool | None = None
    tally_version: str | None = None
    connector_version: str | None = None
    connector_build_sha: str | None = None
    connector_built_at: str | None = None
    queued_outbound_count: int | None = None


class SyncTriggerResponse(TaxMindBooksBase):
    """`POST /connector/sync/{company_id}` 202 response."""

    task_id: UUID
    status: str
    estimated_duration_seconds: int
