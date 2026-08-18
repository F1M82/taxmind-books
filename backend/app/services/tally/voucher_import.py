"""P3.7 Phase 5 — Tally voucher import planner (DRY-RUN ONLY).

Read-only classifier that turns a batch of Tally voucher export rows (the
``VoucherExportRow`` shape produced by the connector's ``export_vouchers``
command) into a classification report. It never writes to the database,
never touches Tally, and never mutates the voucher dataset — it only
*plans* what a future founder-approved persistence step would do.

Founder-approved identity rule
------------------------------
durable identity  : ``(company_id, tally_guid)``

* same GUID                    → UPDATE existing voucher
* same type+number, diff GUID  → two distinct vouchers (voucher_number is
  display-only)
* same GUID, different company → allowed (uniqueness is company-scoped)
* missing / NULL GUID          → SKIP (``MISSING_TALLY_GUID``); no
  substitute durable key is ever used (not number, type+number, date,
  MASTERID, VCHKEY, or anything else).

``tally_voucher_guid`` (the TaxMind REMOTEID / dispatch identity) is
untouched by import — it is not read, written, or repurposed here.

Ledger reconciliation (founder decision #3)
-------------------------------------------
Prefer a per-line Tally ledger MASTERID as the primary key; fall back to
normalized ledger name only when the name resolves unambiguously. A line
that resolves to zero ledgers (missing) or more than one (ambiguous) is
flagged for manual review — never silently attached to an arbitrary
ledger. The current connector export only carries ``ledger_name`` per
line, so name fallback is what actually runs today; ``ledger_master_id``
support is forward-compatible for a future connector FETCH extension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.models.ledger import Ledger
from app.models.voucher import Voucher, VoucherType

logger = logging.getLogger("app.services.tally.voucher_import")

# Known Tally voucher types — the closed set the model enum can store.
_VOUCHER_TYPE_VALUES = frozenset(m.value for m in VoucherType)


class Disposition(str, Enum):
    """What a future persist step would do with a voucher row."""

    INSERT = "insert"
    UPDATE = "update"
    SKIP_MISSING_GUID = "skip_missing_guid"
    SKIP_DUPLICATE_GUID = "skip_duplicate_guid_in_batch"
    MALFORMED = "malformed"


class LedgerMatch(str, Enum):
    """How one voucher ledger line resolved against the local chart."""

    MASTER_ID = "master_id"
    NAME = "name"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


@dataclass(frozen=True)
class PlannedEntry:
    """A single planned voucher ledger line (reconciliation outcome only)."""

    ledger_name: str
    ledger_match: LedgerMatch
    ledger_id: UUID | None


@dataclass(frozen=True)
class PlannedVoucher:
    """The classification of one exported voucher row (no financial detail)."""

    index: int
    tally_guid: str | None
    voucher_type: str
    voucher_number: str | None
    disposition: Disposition
    is_known_type: bool
    is_cancelled: bool
    is_deleted: bool
    is_optional: bool
    existing_voucher_id: UUID | None
    entries: tuple[PlannedEntry, ...]
    planned_source: str = "tally_sync"
    planned_status: str = "posted"


@dataclass
class DryRunReport:
    """Aggregate classification report. Counts only — no amounts, no
    narrative, no party detail (founder: no financial data in artifacts)."""

    company_id: UUID
    total: int = 0
    valid: int = 0
    missing_guid: int = 0
    duplicate_guid_in_batch: int = 0
    insert: int = 0
    update: int = 0
    duplicate_number_different_guid: int = 0
    unknown_type: int = 0
    cancelled: int = 0
    deleted: int = 0
    optional: int = 0
    malformed: int = 0
    parse_failure: int = 0
    manual_review: int = 0
    ledger_match_master_id: int = 0
    ledger_match_name: int = 0
    ledger_ambiguous: int = 0
    ledger_missing: int = 0
    planned: list[PlannedVoucher] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "total": self.total,
            "valid": self.valid,
            "missing_guid": self.missing_guid,
            "duplicate_guid_in_batch": self.duplicate_guid_in_batch,
            "insert": self.insert,
            "update": self.update,
            "duplicate_number_different_guid": self.duplicate_number_different_guid,
            "unknown_type": self.unknown_type,
            "cancelled": self.cancelled,
            "deleted": self.deleted,
            "optional": self.optional,
            "malformed": self.malformed,
            "parse_failure": self.parse_failure,
            "manual_review": self.manual_review,
            "ledger_match_master_id": self.ledger_match_master_id,
            "ledger_match_name": self.ledger_match_name,
            "ledger_ambiguous": self.ledger_ambiguous,
            "ledger_missing": self.ledger_missing,
        }


def _normalize(name: str) -> str:
    return name.strip().lower()


def _is_known_type(raw_type: str) -> bool:
    return raw_type in _VOUCHER_TYPE_VALUES


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _preload_existing(
    db: Session, company_id: UUID
) -> dict[str, UUID]:
    """Map tally_guid → existing voucher id for the company (read-only)."""
    rows = (
        db.query(Voucher.id, Voucher.tally_guid)
        .filter(
            Voucher.company_id == company_id,
            Voucher.tally_guid.isnot(None),
        )
        .all()
    )
    return {guid: vid for vid, guid in rows if guid}


def _preload_number_to_guids(
    db: Session, company_id: UUID
) -> dict[tuple[str, str], set[str]]:
    """Map (voucher_type, voucher_number) → set of existing tally_guids.

    Used to detect "same display number, different durable identity" that
    spans the existing dataset and the incoming batch (the VAC/25-26/222
    case where one GUID is already persisted and a second arrives).
    """
    rows = (
        db.query(Voucher.voucher_type, Voucher.voucher_number, Voucher.tally_guid)
        .filter(
            Voucher.company_id == company_id,
            Voucher.tally_guid.isnot(None),
            Voucher.voucher_number.isnot(None),
        )
        .all()
    )
    out: dict[tuple[str, str], set[str]] = {}
    for vtype, vnumber, guid in rows:
        key = (vtype.value, vnumber)
        out[key] = out.get(key, set()) | {guid}
    return out


def _preload_ledgers(
    db: Session, company_id: UUID
) -> tuple[dict[str, list[UUID]], dict[str, list[UUID]]]:
    """Build (by_master_id, by_name) index maps over the company's ledgers.

    Both values are lists so an ambiguous match (multiple rows sharing a
    normalized name, or a duplicated master id) is detectable rather than
    silently picking the first row.
    """
    rows = (
        db.query(Ledger.id, Ledger.name_normalized, Ledger.tally_master_id)
        .filter(Ledger.company_id == company_id)
        .all()
    )
    by_master: dict[str, list[UUID]] = {}
    by_name: dict[str, list[UUID]] = {}
    for lid, name_norm, master_id in rows:
        if master_id:
            by_master.setdefault(master_id, []).append(lid)
        by_name.setdefault(name_norm, []).append(lid)
    return by_master, by_name


def _reconcile_entry(
    entry: dict[str, Any],
    by_master: dict[str, list[UUID]],
    by_name: dict[str, list[UUID]],
) -> PlannedEntry:
    ledger_name = str(entry.get("ledger_name") or "").strip()
    ledger_guid = (
        entry.get("ledger_guid")
        or entry.get("ledger_master_id")
        or entry.get("master_id")
    )
    if ledger_guid:
        ids = by_master.get(str(ledger_guid), [])
        if len(ids) == 1:
            return PlannedEntry(ledger_name, LedgerMatch.MASTER_ID, ids[0])
        if len(ids) > 1:
            return PlannedEntry(ledger_name, LedgerMatch.AMBIGUOUS, None)

    if ledger_name:
        ids = by_name.get(_normalize(ledger_name), [])
        if len(ids) == 1:
            return PlannedEntry(ledger_name, LedgerMatch.NAME, ids[0])
        if len(ids) > 1:
            return PlannedEntry(ledger_name, LedgerMatch.AMBIGUOUS, None)

    return PlannedEntry(ledger_name, LedgerMatch.MISSING, None)


def _planned(
    index: int,
    *,
    tally_guid: str | None,
    voucher_type: str,
    voucher_number: str | None,
    disposition: Disposition,
    raw: dict[str, Any],
    existing_voucher_id: UUID | None,
    entries: tuple[PlannedEntry, ...],
) -> PlannedVoucher:
    return PlannedVoucher(
        index=index,
        tally_guid=tally_guid,
        voucher_type=voucher_type,
        voucher_number=voucher_number,
        disposition=disposition,
        is_known_type=_is_known_type(voucher_type),
        is_cancelled=bool(raw.get("is_cancelled")),
        is_deleted=bool(raw.get("is_deleted")),
        is_optional=bool(raw.get("is_optional")),
        existing_voucher_id=existing_voucher_id,
        entries=entries,
    )


def _append_skip(
    report: DryRunReport,
    *,
    index: int,
    tally_guid: str | None,
    voucher_type: str,
    voucher_number: str | None,
    disposition: Disposition,
    raw: dict[str, Any],
    existing_voucher_id: UUID | None,
) -> None:
    report.planned.append(
        _planned(
            index,
            tally_guid=tally_guid,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            disposition=disposition,
            raw=raw,
            existing_voucher_id=existing_voucher_id,
            entries=(),
        )
    )


def _count_entries(
    report: DryRunReport, entries: tuple[PlannedEntry, ...]
) -> bool:
    """Tally ledger-line reconciliation outcomes; return `needs_review`."""
    needs_review = False
    for e in entries:
        if e.ledger_match is LedgerMatch.MASTER_ID:
            report.ledger_match_master_id += 1
        elif e.ledger_match is LedgerMatch.NAME:
            report.ledger_match_name += 1
        elif e.ledger_match is LedgerMatch.AMBIGUOUS:
            report.ledger_ambiguous += 1
            needs_review = True
        else:
            report.ledger_missing += 1
            needs_review = True
    return needs_review


def plan_voucher_import(
    db: Session,
    *,
    company_id: UUID,
    rows: list[dict[str, Any]],
) -> DryRunReport:
    """Classify an ``export_vouchers`` batch without writing anything.

    ``rows`` is a list of ``VoucherExportRow``-shaped dicts (the connector's
    ``_export_row_to_dict`` output). Returns an aggregate ``DryRunReport``.
    This function performs SELECT-only database reads and never mutates the
    session, the vouchers, or the ledgers.
    """
    report = DryRunReport(company_id=company_id)
    existing = _preload_existing(db, company_id)
    by_master, by_name = _preload_ledgers(db, company_id)

    seen_guid: dict[str, int] = {}
    # (voucher_type, voucher_number) → set of distinct tally_guids, to
    # detect "same display number, different durable identity" — seeded
    # with the existing dataset so a persisted GUID collides with a new one.
    number_to_guids = _preload_number_to_guids(db, company_id)

    for index, raw in enumerate(rows):
        report.total += 1
        _classify_row(
            index,
            raw,
            report,
            existing,
            by_master,
            by_name,
            seen_guid,
            number_to_guids,
        )

    # Duplicate-number findings: (type, number) keys mapping to >1 GUID.
    report.duplicate_number_different_guid = sum(
        1 for guids in number_to_guids.values() if len(guids) > 1
    )

    return report


def _classify_row(
    index: int,
    raw: Any,
    report: DryRunReport,
    existing: dict[str, UUID],
    by_master: dict[str, list[UUID]],
    by_name: dict[str, list[UUID]],
    seen_guid: dict[str, int],
    number_to_guids: dict[tuple[str, str], set[str]],
) -> None:
    if not isinstance(raw, dict):
        report.malformed += 1
        report.manual_review += 1
        report.planned.append(
            _planned(
                index,
                tally_guid=None,
                voucher_type="",
                voucher_number=None,
                disposition=Disposition.MALFORMED,
                raw={},
                existing_voucher_id=None,
                entries=(),
            )
        )
        return

    voucher_type = str(raw.get("voucher_type") or "").strip()
    tally_guid = _clean_guid(raw.get("tally_guid"))
    voucher_number = _clean_str(raw.get("voucher_number"))
    parsed_date = _parse_iso_date(raw.get("date"))

    if not voucher_type or parsed_date is None:
        report.malformed += 1
        report.manual_review += 1
        if parsed_date is None:
            report.parse_failure += 1
        _append_skip(
            report,
            index=index,
            tally_guid=tally_guid,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            disposition=Disposition.MALFORMED,
            raw=raw,
            existing_voucher_id=None,
        )
        return

    if tally_guid is None:
        report.missing_guid += 1
        _append_skip(
            report,
            index=index,
            tally_guid=None,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            disposition=Disposition.SKIP_MISSING_GUID,
            raw=raw,
            existing_voucher_id=None,
        )
        return

    if tally_guid in seen_guid:
        report.duplicate_guid_in_batch += 1
        report.manual_review += 1
        _append_skip(
            report,
            index=index,
            tally_guid=tally_guid,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            disposition=Disposition.SKIP_DUPLICATE_GUID,
            raw=raw,
            existing_voucher_id=existing.get(tally_guid),
        )
        return
    seen_guid[tally_guid] = index

    _classify_viable_row(
        index,
        raw,
        report,
        existing,
        by_master,
        by_name,
        number_to_guids,
        voucher_type,
        tally_guid,
        voucher_number,
    )


def _classify_viable_row(
    index: int,
    raw: dict[str, Any],
    report: DryRunReport,
    existing: dict[str, UUID],
    by_master: dict[str, list[UUID]],
    by_name: dict[str, list[UUID]],
    number_to_guids: dict[tuple[str, str], set[str]],
    voucher_type: str,
    tally_guid: str,
    voucher_number: str | None,
) -> None:
    # Track duplicate display numbers across distinct durable identities.
    if voucher_number:
        key = (voucher_type, voucher_number)
        number_to_guids[key] = number_to_guids.get(key, set()) | {tally_guid}

    entries = tuple(
        _reconcile_entry(e, by_master, by_name)
        for e in raw.get("entries") or []
        if isinstance(e, dict)
    )
    needs_review = _count_entries(report, entries)
    needs_review = _count_state_flags(raw, report) or needs_review

    existing_id = existing.get(tally_guid)
    if existing_id is not None:
        disposition = Disposition.UPDATE
        report.update += 1
    else:
        disposition = Disposition.INSERT
        report.insert += 1

    if needs_review:
        report.manual_review += 1
    else:
        report.valid += 1

    report.planned.append(
        _planned(
            index,
            tally_guid=tally_guid,
            voucher_type=voucher_type,
            voucher_number=voucher_number,
            disposition=disposition,
            raw=raw,
            existing_voucher_id=existing_id,
            entries=entries,
        )
    )


def _count_state_flags(raw: dict[str, Any], report: DryRunReport) -> bool:
    """Tally ISCANCELLED / ISDELETED / ISOPTIONAL + unknown-type flags."""
    needs_review = False
    if not _is_known_type(str(raw.get("voucher_type") or "").strip()):
        report.unknown_type += 1
        needs_review = True
    if bool(raw.get("is_cancelled")):
        report.cancelled += 1
    if bool(raw.get("is_deleted")):
        report.deleted += 1
        needs_review = True
    if bool(raw.get("is_optional")):
        report.optional += 1
    return needs_review


def _clean_guid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------
# Persistence (P3.7 Phase 6B — WRITES)
# ---------------------------------------------------------------------
#
# `plan_voucher_import` above is READ-ONLY. This section is the write
# path, kept behind an explicit `persist_voucher_import` boundary so the
# dry-run can never write. The heavy lifting is `VoucherService
# .upsert_from_tally`, which enforces the durable identity
# (company_id, tally_guid) and the field mapping.


@dataclass
class PersistReport:
    """Outcome of a persistence run. Counts only — no financial detail."""

    company_id: UUID
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_missing_guid: int = 0
    skipped_duplicate_guid_in_batch: int = 0
    manual_review: int = 0
    malformed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id),
            "total": self.total,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped_missing_guid": self.skipped_missing_guid,
            "skipped_duplicate_guid_in_batch": self.skipped_duplicate_guid_in_batch,
            "manual_review": self.manual_review,
            "malformed": self.malformed,
        }


def _resolve_entries(
    raw_entries: list[Any],
    by_master: dict[str, list[UUID]],
    by_name: dict[str, list[UUID]],
) -> tuple[list[dict[str, Any]], bool]:
    """Map raw ledger lines to (ledger_id, amount, entry_type).

    Returns ``(resolved, needs_review)``. ``needs_review`` is True when any
    line is ambiguous or missing — the caller routes the whole voucher to
    manual review and MUST NOT persist it. Amounts are stored as positive
    Decimals with Dr/Cr in `entry_type` (matching LedgerEntry's
    ``amount > 0`` constraint); the Tally export's signed Dr+/Cr- sign is
    folded into `entry_type`.
    """
    resolved: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        planned = _reconcile_entry(raw, by_master, by_name)
        if (
            planned.ledger_match is LedgerMatch.AMBIGUOUS
            or planned.ledger_match is LedgerMatch.MISSING
            or planned.ledger_id is None
        ):
            return [], True
        resolved.append(
            {
                "ledger_id": planned.ledger_id,
                "amount": abs(Decimal(str(raw.get("amount") or "0"))),
                "entry_type": raw.get("entry_type") or "Dr",
            }
        )
    return resolved, False


def persist_voucher_import(  # audit-exempt: delegates each write to VoucherService.upsert_from_tally, which emits voucher.created/updated in the same transaction
    db: Session,
    *,
    company_id: UUID,
    rows: list[dict[str, Any]],
    audit: AuditEmitter,
) -> PersistReport:
    """Persist a classified ``export_vouchers`` batch (WRITE path).

    Durable identity is ``(company_id, tally_guid)``. Rows are classified
    exactly as the dry-run does, then viable rows are persisted atomically
    (one voucher + its ledger entries per `VoucherService.upsert_from_tally`
    call) within the caller's transaction. This function does NOT commit —
    the caller owns the transaction boundary and rolls back on failure so
    no partial voucher can survive.

    Skip/route rules (never a durable-identity substitution):
      * NULL/missing tally_guid → SKIP (MISSING_TALLY_GUID)
      * duplicate tally_guid within the batch → SKIP (DUPLICATE_GUID_IN_BATCH)
      * malformed (no type/date, or bad date) → SKIP (malformed)
      * ISDELETED=Yes → MANUAL REVIEW (not persisted as a posted voucher)
      * ambiguous/missing ledger line → MANUAL REVIEW
    """
    from app.services.voucher_service import VoucherService

    report = PersistReport(company_id=company_id)
    service = VoucherService(db, audit, company_id=company_id)
    existing = _preload_existing(db, company_id)
    by_master, by_name = _preload_ledgers(db, company_id)

    seen_guid: set[str] = set()

    for raw in rows:
        report.total += 1

        if not isinstance(raw, dict):
            report.malformed += 1
            continue

        raw_type = str(raw.get("voucher_type") or "").strip()
        parsed_date = _parse_iso_date(raw.get("date"))
        if not raw_type or parsed_date is None:
            report.malformed += 1
            continue

        tally_guid = _clean_guid(raw.get("tally_guid"))
        if tally_guid is None:
            report.skipped_missing_guid += 1
            continue

        if tally_guid in seen_guid:
            report.skipped_duplicate_guid_in_batch += 1
            continue
        seen_guid.add(tally_guid)

        if bool(raw.get("is_deleted")):
            report.manual_review += 1
            continue

        resolved, needs_review = _resolve_entries(
            raw.get("entries") or [], by_master, by_name
        )
        if needs_review:
            report.manual_review += 1
            continue

        mapped_type = (
            VoucherType(raw_type) if _is_known_type(raw_type) else None
        )
        is_update = tally_guid in existing
        dr_total = sum(
            (e["amount"] for e in resolved if e["entry_type"] == "Dr"),
            start=Decimal("0"),
        )

        created = service.upsert_from_tally(
            tally_guid=tally_guid,
            voucher_type=mapped_type,
            tally_voucher_type=raw_type,
            voucher_number=_clean_str(raw.get("voucher_number")),
            date=parsed_date,
            narration=_clean_str(raw.get("narration")),
            reference=_clean_str(raw.get("reference")),
            total_amount=dr_total,
            tally_master_id=_clean_str(raw.get("master_id")),
            tally_vchkey=_clean_str(raw.get("vchkey")),
            tally_alter_id=_clean_str(raw.get("alter_id")),
            tally_is_cancelled=_flag(raw.get("is_cancelled")),
            tally_is_deleted=_flag(raw.get("is_deleted")),
            tally_is_optional=_flag(raw.get("is_optional")),
            entries=resolved,
        )
        if is_update:
            report.updated += 1
        else:
            report.inserted += 1
            existing[tally_guid] = created.id

    return report


def _flag(value: Any) -> bool | None:
    """Normalize a Tally origin flag to bool | None (None = not supplied)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in ("yes", "true", "1"):
            return True
        if stripped in ("no", "false", "0"):
            return False
    return None
