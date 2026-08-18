"""P3.7 Phase 7A/7B — Tally ledger master-sync planner (DRY-RUN ONLY).

Read-only classifier that turns a batch of Tally ledger master payload rows
(the ``{"name", "group_name", "gstin", "master_id"}`` shape produced by the
connector's ``sync_masters`` command) into a classification report. It never
writes to the database, never touches Tally, and never mutates the ledger
dataset — it only *plans* what a future founder-approved master persistence
step would do.

Founder-approved identity rules (Phase 7A)
------------------------------------------
durable identity  : ``(company_id, Tally ledger GUID)``, stored in
``Ledger.tally_master_id`` (which holds the Tally GUID despite its historical
name). The field is NOT renamed.

* name is NEVER used as the durable identity.
* two different Tally GUIDs are NEVER merged just because their names match
  (the schema's ``(company_id, name)`` unique key means a same-name row cannot
  be a separate insert either — such rows are flagged for manual review, not
  resolved automatically).
* normalized name is only a *fallback heuristic* for reconciliation state,
  used only to report name-only / ambiguous / unresolved candidates.

Company mapping (Phase 7B)
--------------------------
The company identity + mapping gate lives in
``app.services.tally.company_mapping`` (``resolve_company_mapping`` /
``assert_company_mapping_safe`` / ``require_safe_company_mapping`` /
``confirm_company_mapping``). This module re-exports the read-only pieces for
backward compatibility with Phase 7A callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ledger import Ledger
from app.services.tally.company_mapping import (
    CompanyMappingError,
    CompanyMappingResult,
    assert_company_mapping_safe,
    resolve_company_mapping,
)

__all__ = [
    "CompanyMappingError",
    "CompanyMappingResult",
    "LedgerDisposition",
    "MasterSyncDryRunReport",
    "PlannedLedger",
    "assert_company_mapping_safe",
    "plan_ledger_master_sync",
    "resolve_company_mapping",
]

logger = logging.getLogger("app.services.tally.master_sync_planner")


class LedgerDisposition(str, Enum):
    """What a future master-persist step would do with a ledger row."""

    NEW = "new"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    DUPLICATE_GUID = "duplicate_guid_in_batch"
    MISSING_GUID = "missing_guid"
    NAME_ONLY = "name_only"
    AMBIGUOUS_NAME = "ambiguous_name"
    NAME_GUID_CONFLICT = "name_guid_conflict"
    UNRESOLVED = "unresolved"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class PlannedLedger:
    """The classification of one ledger master row (name-level only)."""

    index: int
    name: str
    tally_guid: str | None
    disposition: LedgerDisposition
    existing_ledger_id: UUID | None


@dataclass
class MasterSyncDryRunReport:
    """Aggregate classification report. Counts only — no finance data."""

    company_id: UUID | None = None
    company_mapped: bool = False
    mapping_method: str | None = None
    tally_company_guid: str | None = None
    tally_company_name: str | None = None

    total: int = 0
    valid_guids: int = 0
    missing_guids: int = 0
    duplicate_guids_in_batch: int = 0
    existing_guid_matches: int = 0
    unchanged: int = 0
    changed_candidates: int = 0
    new_candidates: int = 0
    name_only_matches: int = 0
    name_guid_conflicts: int = 0
    ambiguous_name: int = 0
    unresolved: int = 0
    malformed: int = 0
    manual_review: int = 0
    planned: list[PlannedLedger] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id) if self.company_id else None,
            "company_mapped": self.company_mapped,
            "mapping_method": self.mapping_method,
            "tally_company_guid": self.tally_company_guid,
            "tally_company_name": self.tally_company_name,
            "total": self.total,
            "valid_guids": self.valid_guids,
            "missing_guids": self.missing_guids,
            "duplicate_guids_in_batch": self.duplicate_guids_in_batch,
            "existing_guid_matches": self.existing_guid_matches,
            "unchanged": self.unchanged,
            "changed_candidates": self.changed_candidates,
            "new_candidates": self.new_candidates,
            "name_only_matches": self.name_only_matches,
            "name_guid_conflicts": self.name_guid_conflicts,
            "ambiguous_name": self.ambiguous_name,
            "unresolved": self.unresolved,
            "malformed": self.malformed,
            "manual_review": self.manual_review,
        }


# ---------------------------------------------------------------------------
# Ledger classification
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    return name.strip().lower()


def _clean_guid(value: Any) -> str | None:
    g = str(value or "").strip()
    return g or None


def _name_field(raw: dict[str, Any]) -> str:
    return str(raw.get("name") or "").strip()


def _group_field(raw: dict[str, Any]) -> str:
    g = str(raw.get("group_name") or str(raw.get("parent_group") or "")).strip()
    return g or ""


def _gstin_field(raw: dict[str, Any]) -> str | None:
    g = str(raw.get("gstin") or "").strip().upper()
    return g or None


def _preload_ledgers(
    db: Session, company_id: UUID
) -> tuple[
    dict[str, list[tuple[UUID, str, str | None, str | None]]],
    dict[str, list[tuple[UUID, str | None]]],
]:
    """Read-only index maps over the company's ledgers.

    ``by_guid``: tally_master_id → [(ledger_id, name_normalized, group_name,
    gstin)] (list-form so a duplicated master id is detectable defensively).
    ``by_name``:  name_normalized → [(ledger_id, tally_master_id)].
    """
    rows = (
        db.query(
            Ledger.id,
            Ledger.name_normalized,
            Ledger.tally_master_id,
            Ledger.group_name,
            Ledger.gstin,
        )
        .filter(Ledger.company_id == company_id)
        .all()
    )
    by_guid: dict[str, list[tuple[UUID, str, str | None, str | None]]] = {}
    by_name: dict[str, list[tuple[UUID, str | None]]] = {}
    for lid, name_norm, master_id, group_name, gstin in rows:
        by_name.setdefault(name_norm, []).append((lid, master_id))
        if master_id:
            by_guid.setdefault(master_id, []).append((lid, name_norm, group_name, gstin))
    return by_guid, by_name


def plan_ledger_master_sync(
    db: Session,
    *,
    company_id: UUID,
    ledgers: list[dict[str, Any]],
    tally_company_guid: str | None = None,
    tally_company_name: str | None = None,
) -> MasterSyncDryRunReport:
    """Classify a ``sync_masters`` ledger batch against a local company.

    ``ledgers`` is a list of ``{"name", "group_name", "gstin", "master_id"}``
    dicts (the connector's ``_handle_sync_masters`` wire shape). Returns an
    aggregate ``MasterSyncDryRunReport``. SELECT-only: never mutates the
    session, companies, or ledgers. ``company_id`` is the caller's explicit
    (operator-chosen) company decision; the mapping hard-stop gate is a
    separate step.
    """
    report = MasterSyncDryRunReport(
        company_id=company_id,
        company_mapped=True,
        mapping_method="caller_supplied",
        tally_company_guid=tally_company_guid,
        tally_company_name=tally_company_name,
    )
    by_guid, by_name = _preload_ledgers(db, company_id)
    _classify_batch(report, ledgers, by_guid, by_name)
    return report


def _classify_batch(  # audit-exempt: read-only; seen_guid is a local set, not a db mutation
    report: MasterSyncDryRunReport,
    ledgers: list[dict[str, Any]],
    by_guid: dict[str, list[tuple[UUID, str, str | None, str | None]]],
    by_name: dict[str, list[tuple[UUID, str | None]]],
) -> None:
    seen_guid: set[str] = set()

    for index, raw in enumerate(ledgers):
        report.total += 1
        if not isinstance(raw, dict):
            report.malformed += 1
            report.manual_review += 1
            report.planned.append(
                PlannedLedger(index, "", None, LedgerDisposition.MALFORMED, None)
            )
            continue

        name = _name_field(raw)
        guid = _clean_guid(raw.get("master_id") or raw.get("tally_guid"))
        if not name and guid is None:
            report.malformed += 1
            report.manual_review += 1
            report.planned.append(
                PlannedLedger(index, "", None, LedgerDisposition.MALFORMED, None)
            )
            continue

        if guid is None:
            _classify_missing_guid(report, index, name, by_name)
            continue

        if guid in seen_guid:
            report.duplicate_guids_in_batch += 1
            report.manual_review += 1
            report.planned.append(
                PlannedLedger(index, name, guid, LedgerDisposition.DUPLICATE_GUID, None)
            )
            continue
        seen_guid.add(guid)
        report.valid_guids += 1

        _classify_guided(report, index, name, guid, raw, by_guid, by_name)


def _classify_missing_guid(
    report: MasterSyncDryRunReport,
    index: int,
    name: str,
    by_name: dict[str, list[tuple[UUID, str | None]]],
) -> None:
    report.missing_guids += 1
    report.manual_review += 1
    if not name:
        report.unresolved += 1
        report.planned.append(
            PlannedLedger(index, "", None, LedgerDisposition.UNRESOLVED, None)
        )
        return
    matches = by_name.get(_normalize(name), [])
    if len(matches) == 1:
        report.name_only_matches += 1
        report.planned.append(
            PlannedLedger(
                index, name, None, LedgerDisposition.NAME_ONLY, matches[0][0]
            )
        )
    elif len(matches) > 1:
        report.ambiguous_name += 1
        report.planned.append(
            PlannedLedger(index, name, None, LedgerDisposition.AMBIGUOUS_NAME, None)
        )
    else:
        report.unresolved += 1
        report.planned.append(
            PlannedLedger(index, name, None, LedgerDisposition.UNRESOLVED, None)
        )


def _classify_guided(
    report: MasterSyncDryRunReport,
    index: int,
    name: str,
    guid: str,
    raw: dict[str, Any],
    by_guid: dict[str, list[tuple[UUID, str, str | None, str | None]]],
    by_name: dict[str, list[tuple[UUID, str | None]]],
) -> None:
    guid_matches = by_guid.get(guid, [])

    if guid_matches:
        # Healthy DB: exactly one (uq_ledgers_company_tally). List-form is
        # defensive; more than one is a data-integrity anomaly.
        lid, local_name_norm, local_group, local_gstin = guid_matches[0]
        report.existing_guid_matches += 1
        changed = _fields_changed(raw, name, local_name_norm, local_group, local_gstin)
        if changed:
            report.changed_candidates += 1
            report.planned.append(
                PlannedLedger(index, name, guid, LedgerDisposition.UPDATE, lid)
            )
        else:
            report.unchanged += 1
            report.planned.append(
                PlannedLedger(index, name, guid, LedgerDisposition.UNCHANGED, lid)
            )
        return

    # New GUID against the local chart — resolve the name collision space.
    if not name:
        report.new_candidates += 1
        report.planned.append(
            PlannedLedger(index, name, guid, LedgerDisposition.NEW, None)
        )
        return

    matches = by_name.get(_normalize(name), [])
    if len(matches) == 1:
        lid, local_guid = matches[0]
        if local_guid in (None, ""):
            # Local row exists with no GUID; payload would attach a GUID to an
            # existing row on the strength of a name match only → review.
            report.name_only_matches += 1
            report.manual_review += 1
            report.planned.append(
                PlannedLedger(index, name, guid, LedgerDisposition.NAME_ONLY, lid)
            )
        else:
            # Same name, different durable GUID: never merge, and the schema's
            # (company_id, name) unique key forbids a separate insert → review.
            report.name_guid_conflicts += 1
            report.manual_review += 1
            report.planned.append(
                PlannedLedger(
                    index, name, guid, LedgerDisposition.NAME_GUID_CONFLICT, lid
                )
            )
    elif len(matches) > 1:
        report.ambiguous_name += 1
        report.manual_review += 1
        report.planned.append(
            PlannedLedger(index, name, guid, LedgerDisposition.AMBIGUOUS_NAME, None)
        )
    else:
        report.new_candidates += 1
        report.planned.append(
            PlannedLedger(index, name, guid, LedgerDisposition.NEW, None)
        )


def _fields_changed(
    raw: dict[str, Any],
    name: str,
    local_name_norm: str | None,
    local_group: str | None,
    local_gstin: str | None,
) -> bool:
    name_norm = _normalize(name)
    group = _group_field(raw)
    gstin = _gstin_field(raw)
    if name_norm != (local_name_norm or ""):
        return True
    if group.strip().lower() != (local_group or "").strip().lower():
        return True
    if (gstin or "") != (local_gstin or "").strip().upper():
        return True
    return False
