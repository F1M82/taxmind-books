"""P3.7 Phase 7B — Tally company identity mapping service.

Durable mapping between a Tally company GUID and a local ``Company``. This
is the fail-closed identity boundary that Phase 7A flagged as BLOCKED:

    Tally company GUID → local Company → company_id → ledger persistence

Governing principle: **no identity proof → no persistence.**

* A Tally company GUID is the ONLY trusted key for automatic attachment.
* Name equality alone never silently maps a company; it can only surface a
  single *candidate* requiring operator confirmation.
* No company is ever auto-created, and no arbitrary company is ever
  selected (``assert_company_mapping_safe`` / ``require_safe_company_mapping``
  are the hard-stop gates).

The decision matrix is:

    GUID match (unique)                      → SAFE
    no GUID match + exactly one name match   → MANUAL_REVIEW
    no GUID match + zero name matches        → BLOCKED
    no GUID match + multiple name matches    → AMBIGUOUS
    name→company already bound to another    → CONFLICT
    GUID bound to a different company        → CONFLICT
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import Conflict
from app.models.company import Company


class CompanyMappingStatus(str):
    """Outcome of reconciling a Tally company to a local company.

    ``str``-based so it serialises like the rest of the domain enums while
    staying a closed, documented set. Not an ``enum.Enum`` on purpose — this
    is a transient decision, not a persisted type.
    """

    SAFE = "safe"
    MANUAL_REVIEW = "manual_review"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"
    CONFLICT = "conflict"


class CompanyMappingError(Conflict):
    """Fail-closed identity error: cannot be proven, so do not write.

    A ``Conflict`` domain error (HTTP 409) so an operator-facing confirmation
    endpoint surfaces it cleanly; the sync background task also catches it as
    a fail-closed persistence refusal.
    """

    code = "company_mapping_conflict"


class CompanyMappingResult:
    """Deterministic read-only resolution of a Tally company.

    ``mapped`` is a convenience boolean equal to ``status is SAFE``. The
    ``candidate_*`` fields carry the operator-facing options when the
    outcome is not SAFE (single candidate → MANUAL_REVIEW, several →
    AMBIGUOUS).
    """

    def __init__(
        self,
        status: str,
        *,
        tally_company_guid: str | None = None,
        tally_company_name: str | None = None,
        company_id: UUID | None = None,
        candidate_company_id: UUID | None = None,
        candidate_company_ids: tuple[UUID, ...] = (),
        method: str | None = None,
        reason: str = "",
    ) -> None:
        self.status = status
        self.tally_company_guid = tally_company_guid
        self.tally_company_name = tally_company_name
        self.company_id = company_id
        self.candidate_company_id = candidate_company_id
        self.candidate_company_ids = candidate_company_ids
        self.method = method
        self.reason = reason

    @property
    def mapped(self) -> bool:
        return self.status == CompanyMappingStatus.SAFE

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tally_company_guid": self.tally_company_guid,
            "tally_company_name": self.tally_company_name,
            "company_id": str(self.company_id) if self.company_id else None,
            "candidate_company_id": (
                str(self.candidate_company_id)
                if self.candidate_company_id
                else None
            ),
            "candidate_company_ids": [
                str(c) for c in self.candidate_company_ids
            ],
            "method": self.method,
            "reason": self.reason,
        }


def _clean_guid(value: Any) -> str | None:
    g = str(value or "").strip()
    return g or None


def _clean_name(value: Any) -> str | None:
    n = str(value or "").strip()
    return n or None


def resolve_company_mapping(
    db: Session,
    *,
    tally_company_guid: str | None = None,
    tally_company_name: str | None = None,
) -> CompanyMappingResult:
    """Reconcile a Tally company identity to a local company (read-only).

    Never writes, never auto-creates, never selects an arbitrary company.
    """
    guid = _clean_guid(tally_company_guid)
    name = _clean_name(tally_company_name)

    if guid:
        guid_matches = (
            db.query(Company)
            .filter(Company.tally_master_id == guid)
            .all()
        )
        if len(guid_matches) == 1:
            return CompanyMappingResult(
                CompanyMappingStatus.SAFE,
                tally_company_guid=guid,
                tally_company_name=name,
                company_id=guid_matches[0].id,
                method="guid",
            )
        if len(guid_matches) > 1:
            return CompanyMappingResult(
                CompanyMappingStatus.CONFLICT,
                tally_company_guid=guid,
                tally_company_name=name,
                candidate_company_ids=tuple(c.id for c in guid_matches),
                reason="multiple local companies share the same Tally company GUID",
            )

    if name:
        name_matches = (
            db.query(Company)
            .filter(func.lower(func.trim(Company.name)) == name.lower())
            .all()
        )
        if len(name_matches) == 0:
            return CompanyMappingResult(
                CompanyMappingStatus.BLOCKED,
                tally_company_guid=guid,
                tally_company_name=name,
                reason="no local company matches the Tally company GUID or name",
            )
        if len(name_matches) > 1:
            return CompanyMappingResult(
                CompanyMappingStatus.AMBIGUOUS,
                tally_company_guid=guid,
                tally_company_name=name,
                candidate_company_ids=tuple(c.id for c in name_matches),
                reason="multiple local companies share the exact Tally company name",
            )
        candidate = name_matches[0]
        if candidate.tally_master_id is not None:
            return CompanyMappingResult(
                CompanyMappingStatus.CONFLICT,
                tally_company_guid=guid,
                tally_company_name=name,
                candidate_company_id=candidate.id,
                reason=(
                    "name matches a local company already bound to a "
                    "different Tally company GUID"
                ),
            )
        return CompanyMappingResult(
            CompanyMappingStatus.MANUAL_REVIEW,
            tally_company_guid=guid,
            tally_company_name=name,
            candidate_company_id=candidate.id,
            method="exact_name",
            reason=(
                "exactly one name candidate with no Tally GUID yet; "
                "operator confirmation required"
            ),
        )

    return CompanyMappingResult(
        CompanyMappingStatus.BLOCKED,
        tally_company_guid=guid,
        tally_company_name=name,
        reason="no Tally company GUID or name supplied",
    )


def assert_company_mapping_safe(
    db: Session,
    *,
    company_id: UUID,
    tally_company_guid: str | None = None,
    tally_company_name: str | None = None,
) -> None:
    """HARD-STOP gate (read-only). Raise unless ``company_id`` is provably
    the mapped Tally company.

    * SAFE but resolved to a different ``company_id`` → mismatch, raise.
    * MANUAL_REVIEW → requires operator confirmation, raise (never silent).
    * anything else (BLOCKED / AMBIGUOUS / CONFLICT) → raise.
    """
    result = resolve_company_mapping(
        db,
        tally_company_guid=tally_company_guid,
        tally_company_name=tally_company_name,
    )
    if result.status == CompanyMappingStatus.SAFE:
        if result.company_id == company_id:
            return
        raise CompanyMappingError(
            "company mismatch: chosen company_id does not match the local "
            "company bound to the Tally company GUID"
        )
    if (
        result.status == CompanyMappingStatus.MANUAL_REVIEW
        and result.candidate_company_id == company_id
    ):
        raise CompanyMappingError(
            "company mapping requires operator confirmation (no silent "
            "attachment on name alone)"
        )
    raise CompanyMappingError(
        "cannot confirm mapping: no arbitrary company assignment"
    )


def require_safe_company_mapping(
    db: Session,
    *,
    company_id: UUID,
    tally_company_guid: str | None,
) -> Company:
    """Persistence gate (read-only). Return the local company only when the
    sync payload's Tally company GUID deterministically matches it.

    Raises ``CompanyMappingError`` (→ no write) when: the GUID is missing,
    the company does not exist, the company is not yet mapped, or the
    company is mapped to a *different* GUID than the payload claims.
    """
    guid = _clean_guid(tally_company_guid)
    if guid is None:
        raise CompanyMappingError(
            "no Tally company GUID in sync payload; refusing ledger persistence"
        )
    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise CompanyMappingError(f"company {company_id} not found")
    if company.tally_master_id is None:
        raise CompanyMappingError(
            "company is not mapped to a Tally company; operator confirmation "
            "is required before ledger persistence"
        )
    if company.tally_master_id != guid:
        raise CompanyMappingError(
            "company is mapped to a different Tally company GUID than the "
            "sync payload; refusing ledger persistence"
        )
    return company


def confirm_company_mapping(
    db: Session,
    *,
    company_id: UUID,
    tally_company_guid: str,
    tally_company_name: str | None = None,
    audit: AuditEmitter,
) -> Company:
    """Operator confirmation: bind ``company_id`` to the Tally company GUID.

    The operator explicitly selects the company (``company_id``) AND supplies
    the Tally GUID — the GUID is never inferred from the name. Refuses to
    overwrite a different GUID or to bind a GUID already held by another
    company (both are ``CompanyMappingError``). Idempotent. Emits
    ``company.tally_mapping_configured`` (first binding) or
    ``company.tally_mapping_changed``.
    """
    guid = _clean_guid(tally_company_guid)
    if guid is None:
        raise CompanyMappingError("Tally company GUID is required to confirm")
    company = db.query(Company).filter(Company.id == company_id).first()
    if company is None:
        raise CompanyMappingError(f"company {company_id} not found")
    if company.tally_master_id is not None and company.tally_master_id != guid:
        raise CompanyMappingError(
            "company is already mapped to a different Tally company GUID"
        )
    other = (
        db.query(Company)
        .filter(Company.tally_master_id == guid, Company.id != company_id)
        .first()
    )
    if other is not None:
        raise CompanyMappingError(
            "Tally company GUID is already mapped to another local company"
        )
    if company.tally_master_id == guid:
        return company  # idempotent; already bound

    old = company.tally_master_id
    company.tally_master_id = guid
    db.flush()
    audit.emit(
        action=(
            "company.tally_mapping_configured"
            if old is None
            else "company.tally_mapping_changed"
        ),
        entity_type="company",
        entity_id=company.id,
        old_value={"tally_master_id": old},
        new_value={
            "tally_master_id": guid,
            "tally_company_name": _clean_name(tally_company_name),
        },
        company_id_override=company.id,
    )
    return company
