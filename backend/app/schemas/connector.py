"""Connector enrollment + status schemas (P0.23)."""

from __future__ import annotations

from datetime import date, datetime
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
    connector_id: UUID | None = None
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


class CompanyMappingConfirmRequest(TaxMindBooksBase):
    """Operator confirmation of the active company's Tally company GUID.

    The GUID is supplied explicitly by the operator (never inferred from the
    name); the company is the active `X-Company-ID` company.
    """

    tally_company_guid: str
    tally_company_name: str | None = None


class CompanyMappingConfirmResponse(TaxMindBooksBase):
    """Result of a confirmed company ↔ Tally GUID binding."""

    company_id: UUID
    tally_master_id: str
    tally_company_name: str | None = None


class CompanyMappingStatusOut(TaxMindBooksBase):
    """Read-only status of the active company's Tally mapping."""

    company_id: UUID
    tally_master_id: str | None = None
    mapped: bool


class OpeningBalanceSeedRequest(TaxMindBooksBase):
    """Request body for ``POST /connector/opening-balance-seed/{company_id}``.

    ``anchor_date`` is the start of the earliest imported financial year
    (see ``docs/PHASE_3_OPENING_BALANCE_ARCHITECTURE.md``). It is locked in
    on the company's first successful seed run; a later call naming a
    different anchor is refused.
    """

    anchor_date: date


class OpeningBalanceSeedTriggerResponse(TaxMindBooksBase):
    """``POST /connector/opening-balance-seed/{company_id}`` 202 response."""

    task_id: UUID
    status: str
    anchor_date: date


class VoucherImportRequest(TaxMindBooksBase):
    """Request body for ``POST /connector/voucher-import/{company_id}``.

    ``dry_run`` defaults to ``True``: the run classifies every voucher and
    reports counts but writes nothing. Set it to ``False`` to persist.
    The range must start on/after the company's opening-balance anchor.
    """

    from_date: date
    to_date: date
    dry_run: bool = True


class VoucherImportTriggerResponse(TaxMindBooksBase):
    """``POST /connector/voucher-import/{company_id}`` 202 response."""

    task_id: UUID
    status: str
    dry_run: bool
    windows_total: int


class VoucherImportErrorOut(TaxMindBooksBase):
    code: str
    message: str


class VoucherImportRunOut(TaxMindBooksBase):
    """``GET /connector/voucher-import/{task_id}`` progress/outcome."""

    task_id: UUID
    company_id: UUID
    from_date: date
    to_date: date
    dry_run: bool
    state: str
    windows_total: int
    windows_done: int
    totals: dict[str, int]
    error: VoucherImportErrorOut | None
    started_at: datetime
    finished_at: datetime | None
