"""Dashboard schemas per docs/REPORTS.md §Dashboard (P0.40)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.common import Money, TaxMindBooksBase

AlertSeverity = Literal["info", "warning", "critical"]
AlertKind = Literal[
    "connector_offline",
    "connector_stale",
    "tally_not_running",
    "pending_approvals",
]


class DashboardConnector(TaxMindBooksBase):
    connected: bool
    tally_running: bool | None = None
    last_seen_seconds_ago: int | None = None


class DashboardTodayMetrics(TaxMindBooksBase):
    vouchers_created: int
    vouchers_pending_approval: int
    cash_in: Money
    cash_out: Money


class DashboardMonthMetrics(TaxMindBooksBase):
    cash_in: Money
    cash_out: Money
    vouchers_created: int
    vouchers_pending_approval: int


class DashboardOutstanding(TaxMindBooksBase):
    receivables_total: Money
    payables_total: Money


class DashboardGstLiability(TaxMindBooksBase):
    month_to_date: Money


class DashboardAlert(TaxMindBooksBase):
    kind: AlertKind
    severity: AlertSeverity
    message: str
    since: datetime | None = None


class DashboardHomeResponse(TaxMindBooksBase):
    as_of: datetime
    company_name: str
    connector: DashboardConnector
    today: DashboardTodayMetrics
    this_month: DashboardMonthMetrics
    outstanding: DashboardOutstanding
    gst_liability_indicative: DashboardGstLiability
    alerts: list[DashboardAlert]
