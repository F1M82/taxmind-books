"""Historical voucher import orchestration (Phase B).

Drives the connector's read-only ``export_vouchers`` command window by
window and feeds each window to the existing planner
(`voucher_import.plan_voucher_import`, dry-run) or writer
(`voucher_import.persist_voucher_import`). Identity, dedupe and ledger
reconciliation rules live there; this module only sequences windows and
guards the run.

Safety properties
-----------------
* **Dry-run by default** -- nothing is written unless ``dry_run=False``.
* **Anchor guard** -- refuses unless the company's opening balances are
  seeded and the range starts on/after the anchor date; vouchers before the
  anchor are already inside the seeded openings (double count otherwise).
* **Company identity, every window** -- ``get_active_tally_company`` is
  re-read before each window and passed through the same fail-closed
  `require_safe_company_mapping` gate the master sync uses. The export
  reply itself carries no company identity, so this is the only check.
* **Atomic per window** -- each window is committed (or rolled back) on its
  own; a failure stops the run and leaves earlier windows in place. Re-running
  is safe because persistence is idempotent on ``(company_id, tally_guid)``.
* **Fail loudly on the period** -- a connector reply of
  ``tally_period_not_covered`` (Tally's active period doesn't cover the
  window) stops the run with that code instead of importing nothing.

Run state is held in this process (like the connector registry): fine for the
single-instance deployment, lost on restart.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.audit import AuditContext, AuditEmitter
from app.core.database import SessionLocal
from app.core.exceptions import (
    OpeningBalanceNotSeeded,
    VoucherImportBeforeAnchor,
)
from app.models.company import Company
from app.services.tally import connector_registry as _registry_mod
from app.services.tally.company_mapping import (
    CompanyMappingError,
    require_safe_company_mapping,
)
from app.services.tally.voucher_import import (
    persist_voucher_import,
    plan_voucher_import,
)

logger = logging.getLogger("app.services.tally.voucher_import_run")

_MAX_RUNS_KEPT = 50
_EXPORT_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------


def monthly_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split ``[start, end]`` (inclusive) into calendar-month windows.

    Months keep each connector reply small (a full financial year of
    vouchers is one large WebSocket message). The first/last window are
    clipped to the requested range.
    """
    if end < start:
        return []
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
        window_end = min(next_month - timedelta(days=1), end)
        windows.append((cursor, window_end))
        cursor = next_month
    return windows


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------


def check_import_range(
    company: Company, *, from_date: date, to_date: date
) -> None:
    """Raise unless importing ``[from_date, to_date]`` is safe for `company`."""
    anchor = company.opening_balance_anchor_date
    if anchor is None:
        raise OpeningBalanceNotSeeded(
            "Seed the company's opening balances before importing vouchers.",
        )
    if from_date < anchor:
        raise VoucherImportBeforeAnchor(
            "Vouchers dated before the opening-balance anchor are already "
            "inside the seeded openings and cannot be imported.",
            details={
                "anchor_date": anchor.isoformat(),
                "requested_from_date": from_date.isoformat(),
            },
        )
    if to_date < from_date:
        raise VoucherImportBeforeAnchor(
            "to_date is before from_date.",
            details={
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
        )


# ---------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------


@dataclass
class ImportRun:
    task_id: UUID
    company_id: UUID
    from_date: date
    to_date: date
    dry_run: bool
    windows_total: int
    state: str = "running"  # running | completed | failed
    windows_done: int = 0
    totals: dict[str, int] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def add_counts(self, counts: dict[str, Any]) -> None:
        for key, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            self.totals[key] = self.totals.get(key, 0) + value

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id),
            "company_id": str(self.company_id),
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "dry_run": self.dry_run,
            "state": self.state,
            "windows_total": self.windows_total,
            "windows_done": self.windows_done,
            "totals": dict(self.totals),
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
        }


_RUNS: OrderedDict[UUID, ImportRun] = OrderedDict()


def register_run(run: ImportRun) -> None:
    _RUNS[run.task_id] = run
    while len(_RUNS) > _MAX_RUNS_KEPT:
        _RUNS.popitem(last=False)


def get_run(task_id: UUID) -> ImportRun | None:
    return _RUNS.get(task_id)


def clear_runs() -> None:
    """Test helper."""
    _RUNS.clear()


def new_run(
    *, company_id: UUID, from_date: date, to_date: date, dry_run: bool
) -> ImportRun:
    run = ImportRun(
        task_id=uuid4(),
        company_id=company_id,
        from_date=from_date,
        to_date=to_date,
        dry_run=dry_run,
        windows_total=len(monthly_windows(from_date, to_date)),
    )
    register_run(run)
    return run


# ---------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------


class _RunFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _apply_window(
    db: Session,
    *,
    company: Company,
    rows: list[dict[str, Any]],
    dry_run: bool,
    task_id: UUID,
    user_id: UUID | None,
) -> dict[str, Any]:
    if dry_run:
        return plan_voucher_import(
            db, company_id=company.id, rows=rows
        ).to_dict()
    audit = AuditEmitter(
        db,
        AuditContext(
            company=company,
            user=None,
            ip_address=None,
            user_agent="connector-voucher-import/1.0",
            request_id=task_id,
            source="connector",
        ),
    )
    return persist_voucher_import(
        db, company_id=company.id, rows=rows, audit=audit
    ).to_dict()


async def _verify_identity(company_id: UUID) -> None:
    """Re-read the open Tally company and pass it through the mapping gate."""
    registry = _registry_mod.get_registry()
    try:
        active = await registry.send_command(
            company_id=company_id,
            command="get_active_tally_company",
            args={},
            timeout_seconds=20,
        )
    except Exception as exc:
        raise _RunFailed(
            "connector_unreachable", f"could not read the active Tally company: {exc}"
        ) from exc
    if active.get("status") != "success":
        err = active.get("error") or {}
        raise _RunFailed(
            str(err.get("code") or "active_company_unavailable"),
            str(err.get("message") or "could not read the active Tally company"),
        )
    guid = (active.get("result") or {}).get("tally_company_guid")
    db = SessionLocal()
    try:
        require_safe_company_mapping(
            db, company_id=company_id, tally_company_guid=guid
        )
    except CompanyMappingError as exc:
        raise _RunFailed("company_mapping_conflict", str(exc)) from exc
    finally:
        db.close()


async def _run_window(
    run: ImportRun, window: tuple[date, date], user_id: UUID | None
) -> None:
    registry = _registry_mod.get_registry()
    await _verify_identity(run.company_id)
    try:
        reply = await registry.send_command(
            company_id=run.company_id,
            command="export_vouchers",
            args={
                "from_date": _yyyymmdd(window[0]),
                "to_date": _yyyymmdd(window[1]),
            },
            timeout_seconds=_EXPORT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise _RunFailed(
            "connector_unreachable",
            f"export_vouchers {window[0]}..{window[1]} failed: {exc}",
        ) from exc
    if reply.get("status") != "success":
        err = reply.get("error") or {}
        raise _RunFailed(
            str(err.get("code") or "export_failed"),
            str(err.get("message") or "connector export failed"),
        )
    rows = (reply.get("result") or {}).get("vouchers") or []

    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == run.company_id).one()
        counts = _apply_window(
            db,
            company=company,
            rows=rows,
            dry_run=run.dry_run,
            task_id=run.task_id,
            user_id=user_id,
        )
        if run.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    run.add_counts(counts)


async def execute_import_run(run: ImportRun, *, user_id: UUID | None) -> None:
    """Run every window; record progress/outcome on `run`. Never raises."""
    try:
        for window in monthly_windows(run.from_date, run.to_date):
            await _run_window(run, window, user_id)
            run.windows_done += 1
        run.state = "completed"
    except _RunFailed as exc:
        run.state = "failed"
        run.error = {"code": exc.code, "message": exc.message}
        logger.warning(
            "voucher_import %s failed for %s: %s: %s",
            run.task_id, run.company_id, exc.code, exc.message,
        )
    except Exception as exc:
        run.state = "failed"
        run.error = {"code": "internal_error", "message": str(exc)}
        logger.exception(
            "voucher_import %s crashed for %s", run.task_id, run.company_id
        )
    finally:
        run.finished_at = datetime.now(UTC)
        logger.info(
            "voucher_import %s %s for %s: windows %d/%d totals=%s",
            run.task_id, run.state, run.company_id,
            run.windows_done, run.windows_total, run.totals,
        )
