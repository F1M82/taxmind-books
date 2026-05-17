"""Dashboard composition per docs/REPORTS.md §Dashboard (P0.40).

A single function builds the full home-screen payload by combining:

  - Connector snapshot from the in-memory registry (P0.24 / P0.25)
  - Today and current-month voucher / cash metrics
  - Outstanding totals (reuses P0.38 compute_outstanding)
  - Month-to-date indicative GST liability
  - Computed alerts (connector offline, Tally not running, pending
    approvals over threshold)

Universal report rules R1 (Optional excluded), R2 (cancelled excluded)
apply to the financial metrics.

Performance budget per spec: 500ms p95. Phase-0 keeps it simple — five
small aggregations against indexed columns. If we exceed the budget in
production the cache rule (R7) is revisited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.ledger import Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.reporting.outstanding import compute_outstanding

# Import the module, not the symbols, so test fixtures that
# `monkeypatch.setattr(connector_registry, "get_registry", ...)` see
# our late lookup. Direct `from ... import get_registry` would bind
# the name at module load and the patch would miss us. See
# CONNECTOR_PROTOCOL.md §"Patchable singletons" for the convention.
from app.services.tally import connector_registry as _connector_registry_mod

ConnectorRegistry = _connector_registry_mod.ConnectorRegistry

# Thresholds for alert generation. Tunable; not user-facing.
_CONNECTOR_STALE_SECONDS = 60
_PENDING_APPROVALS_ALERT_THRESHOLD = 10

# Group names that count as "cash" for the dashboard cash-flow tile.
_CASH_GROUPS = frozenset({"bank accounts", "cash-in-hand", "bank od a/c"})

# Voucher statuses that are "live in the books" for reporting purposes.
# `pending_tally_post` rows have not been mirrored to Tally yet but are
# real entries the user made; reports show them so the dashboard moves
# the moment a voucher is entered, not when Tally finally accepts it.
# (P0.46d) See trial_balance / profit_loss for the matching filter.
_BOOKS_LIVE_STATUSES = (
    VoucherStatus.posted,
    VoucherStatus.pending_tally_post,
)

# Voucher types whose vouchers.cgst+sgst+igst+cess contribute to output
# GST liability. Same set used by the future GST analytic per REPORTS.md.
_OUTPUT_GST_TYPES = frozenset({VoucherType.Sales, VoucherType.DebitNote})
_INPUT_GST_TYPES = frozenset({VoucherType.Purchase, VoucherType.CreditNote})


@dataclass
class TodayMetrics:
    vouchers_created: int = 0
    vouchers_pending_approval: int = 0
    cash_in: Decimal = Decimal("0")
    cash_out: Decimal = Decimal("0")


@dataclass
class MonthMetrics:
    cash_in: Decimal = Decimal("0")
    cash_out: Decimal = Decimal("0")
    vouchers_created: int = 0
    vouchers_pending_approval: int = 0


@dataclass
class OutstandingTotals:
    receivables_total: Decimal = Decimal("0")
    payables_total: Decimal = Decimal("0")


@dataclass
class ConnectorSnapshot:
    connected: bool
    tally_running: bool | None = None
    last_seen_seconds_ago: int | None = None
    last_seen_at: datetime | None = None


@dataclass
class Alert:
    kind: str
    severity: str
    message: str
    since: datetime | None = None


@dataclass
class DashboardData:
    as_of: datetime
    company_name: str
    connector: ConnectorSnapshot
    today: TodayMetrics
    this_month: MonthMetrics
    outstanding: OutstandingTotals
    gst_liability_month_to_date: Decimal
    alerts: list[Alert] = field(default_factory=list)


# ---------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------


def build_dashboard(  # audit-exempt: read-only aggregation
    db: Session,
    *,
    company: Company,
    registry: ConnectorRegistry | None = None,
    now: datetime | None = None,
) -> DashboardData:
    """Compose the full dashboard payload for `company`.

    `now` is overridable so tests can pin the clock. `registry` is
    overridable so tests can inject a fake. Both default to the
    production singletons.
    """
    now = now or datetime.now(UTC)
    today_local: date = now.date()
    month_start = today_local.replace(day=1)

    connector = _connector_snapshot(
        registry or _connector_registry_mod.get_registry(),
        company.id,
        now=now,
    )

    today_metrics = _metrics_for_range(
        db, company_id=company.id, from_date=today_local, to_date=today_local
    )
    month_metrics_raw = _metrics_for_range(
        db,
        company_id=company.id,
        from_date=month_start,
        to_date=today_local,
    )

    today = TodayMetrics(
        vouchers_created=today_metrics["vouchers_created"],
        vouchers_pending_approval=today_metrics["vouchers_pending_approval"],
        cash_in=today_metrics["cash_in"],
        cash_out=today_metrics["cash_out"],
    )
    this_month = MonthMetrics(
        cash_in=month_metrics_raw["cash_in"],
        cash_out=month_metrics_raw["cash_out"],
        vouchers_created=month_metrics_raw["vouchers_created"],
        vouchers_pending_approval=month_metrics_raw[
            "vouchers_pending_approval"
        ],
    )

    outstanding = _outstanding_totals(
        db, company_id=company.id, as_of_date=today_local
    )

    gst_mtd = _gst_liability_mtd(
        db,
        company_id=company.id,
        from_date=month_start,
        to_date=today_local,
    )

    alerts = _build_alerts(
        connector=connector,
        this_month=this_month,
        now=now,
    )

    return DashboardData(
        as_of=now,
        company_name=company.name,
        connector=connector,
        today=today,
        this_month=this_month,
        outstanding=outstanding,
        gst_liability_month_to_date=gst_mtd,
        alerts=alerts,
    )


# ---------------------------------------------------------------------
# Connector snapshot
# ---------------------------------------------------------------------


def _connector_snapshot(
    registry: ConnectorRegistry,
    company_id: UUID,
    *,
    now: datetime,
) -> ConnectorSnapshot:
    snap: dict[str, Any] | None = registry.status_for(company_id)
    if snap is None:
        return ConnectorSnapshot(connected=False)

    last_seen_raw = snap.get("last_seen_at")
    last_seen_at: datetime | None
    if isinstance(last_seen_raw, str):
        try:
            last_seen_at = datetime.fromisoformat(last_seen_raw)
        except ValueError:
            last_seen_at = None
    elif isinstance(last_seen_raw, datetime):
        last_seen_at = last_seen_raw
    else:
        last_seen_at = None

    seconds_ago: int | None = None
    if last_seen_at is not None:
        # Normalize timezone — the registry always emits UTC ISO; if it
        # somehow comes in naive, treat as UTC to keep the math sane.
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        seconds_ago = max(0, int((now - last_seen_at).total_seconds()))

    return ConnectorSnapshot(
        connected=True,
        tally_running=snap.get("tally_running"),
        last_seen_seconds_ago=seconds_ago,
        last_seen_at=last_seen_at,
    )


# ---------------------------------------------------------------------
# Voucher / cash metrics
# ---------------------------------------------------------------------


def _metrics_for_range(  # audit-exempt: read-only aggregation
    db: Session,
    *,
    company_id: UUID,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    """Counts and cash movement over a closed date range.

    `vouchers_created` counts every non-cancelled voucher created in
    the period (Optional included; the UI surfaces them as items the
    user can approve).

    `vouchers_pending_approval` counts Optional vouchers awaiting
    approval (not yet approved, not rejected, not cancelled).

    `cash_in` / `cash_out` are Dr/Cr movement on Bank/Cash ledgers in
    the period, restricted to Regular posted vouchers per R1/R2.
    """
    created_count = (
        db.query(func.count(Voucher.id))
        .filter(
            Voucher.company_id == company_id,
            Voucher.date >= from_date,
            Voucher.date <= to_date,
            Voucher.status != VoucherStatus.cancelled,
        )
        .scalar()
        or 0
    )

    pending_count = (
        db.query(func.count(Voucher.id))
        .filter(
            Voucher.company_id == company_id,
            Voucher.date >= from_date,
            Voucher.date <= to_date,
            Voucher.is_optional_in_tally.is_(True),
            Voucher.approved_to_regular_at.is_(None),
            Voucher.status != VoucherStatus.cancelled,
            Voucher.status != VoucherStatus.rejected_optional,
        )
        .scalar()
        or 0
    )

    dr_expr = case(
        (LedgerEntry.entry_type == EntryType.Dr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    cr_expr = case(
        (LedgerEntry.entry_type == EntryType.Cr, LedgerEntry.amount),
        else_=Decimal("0"),
    )
    cash_row = (
        db.query(
            func.coalesce(func.sum(dr_expr), Decimal("0")).label("cash_in"),
            func.coalesce(func.sum(cr_expr), Decimal("0")).label("cash_out"),
        )
        .select_from(LedgerEntry)
        .join(Voucher, Voucher.id == LedgerEntry.voucher_id)
        .join(Ledger, Ledger.id == LedgerEntry.ledger_id)
        .filter(
            Voucher.company_id == company_id,
            LedgerEntry.company_id == company_id,
            Ledger.company_id == company_id,
            Voucher.date >= from_date,
            Voucher.date <= to_date,
            # P0.46d: vouchers in `pending_tally_post` are live in the
            # books even though Tally hasn't mirrored them yet — the
            # cash movement they represent is real for dashboard purposes.
            Voucher.status.in_(_BOOKS_LIVE_STATUSES),
            Voucher.is_optional_in_tally.is_(False),
            func.lower(func.trim(Ledger.group_name)).in_(_CASH_GROUPS),
        )
        .one()
    )

    return {
        "vouchers_created": int(created_count),
        "vouchers_pending_approval": int(pending_count),
        "cash_in": Decimal(cash_row.cash_in),
        "cash_out": Decimal(cash_row.cash_out),
    }


# ---------------------------------------------------------------------
# Outstanding totals (reuse P0.38)
# ---------------------------------------------------------------------


def _outstanding_totals(
    db: Session,
    *,
    company_id: UUID,
    as_of_date: date,
) -> OutstandingTotals:
    receivables = compute_outstanding(
        db, company_id=company_id, as_of_date=as_of_date, type_="receivables"
    )
    payables = compute_outstanding(
        db, company_id=company_id, as_of_date=as_of_date, type_="payables"
    )
    return OutstandingTotals(
        receivables_total=receivables.total,
        payables_total=payables.total,
    )


# ---------------------------------------------------------------------
# GST liability month-to-date
# ---------------------------------------------------------------------


def _gst_liability_mtd(  # audit-exempt: read-only aggregation
    db: Session,
    *,
    company_id: UUID,
    from_date: date,
    to_date: date,
) -> Decimal:
    """Indicative net GST liability — output - input, floored at zero.

    Restricted to Regular (R1) posted (R2) vouchers in the date range.
    """
    output_types = [t.value for t in _OUTPUT_GST_TYPES]
    input_types = [t.value for t in _INPUT_GST_TYPES]
    total_expr = (
        Voucher.cgst + Voucher.sgst + Voucher.igst + Voucher.cess
    )
    row = (
        db.query(
            func.coalesce(
                func.sum(
                    case(
                        (Voucher.voucher_type.in_(output_types), total_expr),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("output_total"),
            func.coalesce(
                func.sum(
                    case(
                        (Voucher.voucher_type.in_(input_types), total_expr),
                        else_=Decimal("0"),
                    )
                ),
                Decimal("0"),
            ).label("input_total"),
        )
        .filter(
            Voucher.company_id == company_id,
            Voucher.date >= from_date,
            Voucher.date <= to_date,
            Voucher.status.in_(_BOOKS_LIVE_STATUSES),
            Voucher.is_optional_in_tally.is_(False),
            Voucher.gst_applicable.is_(True),
            or_(
                Voucher.voucher_type.in_(output_types),
                Voucher.voucher_type.in_(input_types),
            ),
        )
        .one()
    )
    net = Decimal(row.output_total) - Decimal(row.input_total)
    return net if net > 0 else Decimal("0")


# ---------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------


def _build_alerts(
    *,
    connector: ConnectorSnapshot,
    this_month: MonthMetrics,
    now: datetime,
) -> list[Alert]:
    alerts: list[Alert] = []

    if not connector.connected:
        alerts.append(
            Alert(
                kind="connector_offline",
                severity="warning",
                message=(
                    "Connector is not connected. Vouchers will queue "
                    "and post when it comes back online."
                ),
                since=None,
            )
        )
    else:
        if connector.tally_running is False:
            alerts.append(
                Alert(
                    kind="tally_not_running",
                    severity="warning",
                    message=(
                        "Connector is online but TallyPrime is not "
                        "running. Start Tally to enable posting."
                    ),
                    since=connector.last_seen_at,
                )
            )
        if (
            connector.last_seen_seconds_ago is not None
            and connector.last_seen_seconds_ago > _CONNECTOR_STALE_SECONDS
        ):
            since = (
                now - timedelta(seconds=connector.last_seen_seconds_ago)
                if connector.last_seen_seconds_ago is not None
                else None
            )
            alerts.append(
                Alert(
                    kind="connector_stale",
                    severity="warning",
                    message=(
                        f"Connector last seen "
                        f"{connector.last_seen_seconds_ago}s ago."
                    ),
                    since=since,
                )
            )

    if (
        this_month.vouchers_pending_approval
        >= _PENDING_APPROVALS_ALERT_THRESHOLD
    ):
        alerts.append(
            Alert(
                kind="pending_approvals",
                severity="info",
                message=(
                    f"{this_month.vouchers_pending_approval} vouchers are "
                    "awaiting approval this month."
                ),
            )
        )

    return alerts


__all__ = [
    "Alert",
    "ConnectorSnapshot",
    "DashboardData",
    "MonthMetrics",
    "OutstandingTotals",
    "TodayMetrics",
    "build_dashboard",
]
