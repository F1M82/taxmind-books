"""Ledger service: CRUD + fuzzy search (P0.17) + sync_masters ingest (P0.46b)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.database import SCOPE_BYPASS_OPTION
from app.core.exceptions import LedgerInUse, LedgerNotFound
from app.models.ledger import BalanceType, Ledger
from app.models.voucher import LedgerEntry
from app.schemas.ledger import LedgerCreate, LedgerUpdate
from app.services.tally.company_mapping import CompanyMappingError

logger = logging.getLogger("app.services.ledger_service")


def _normalize(name: str) -> str:
    return name.strip().lower()


def _clean_guid(value: Any) -> str | None:
    g = str(value or "").strip()
    return g or None


def _ledger_snapshot(led: Ledger) -> dict[str, Any]:
    return {
        "id": str(led.id),
        "company_id": str(led.company_id),
        "name": led.name,
        "name_normalized": led.name_normalized,
        "group_name": led.group_name,
        "parent_ledger_id": (
            str(led.parent_ledger_id) if led.parent_ledger_id else None
        ),
        "opening_balance": str(led.opening_balance),
        "balance_type": (
            led.balance_type.value
            if hasattr(led.balance_type, "value")
            else str(led.balance_type)
        ),
        "gstin": led.gstin,
        "pan": led.pan,
        "phone": led.phone,
        "email": led.email,
        "address": led.address,
        "state_code": led.state_code,
        "is_active": led.is_active,
        "tally_master_id": led.tally_master_id,
    }


class LedgerService:
    """Tenant-scoped ledger CRUD.

    Constructor takes the *company id* explicitly. The service's queries
    filter on `company_id` even though the scoped session would too —
    defense in depth. Also lets the service work with workers that
    don't have a request-scoped session.
    """

    def __init__(
        self, db: Session, audit: AuditEmitter, company_id: UUID
    ) -> None:
        self.db = db
        self.audit = audit
        self.company_id = company_id

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(self, data: LedgerCreate) -> Ledger:
        ledger = Ledger(
            company_id=self.company_id,
            name=data.name,
            name_normalized=_normalize(data.name),
            group_name=data.group_name,
            parent_ledger_id=data.parent_ledger_id,
            opening_balance=data.opening_balance,
            balance_type=BalanceType(data.balance_type),
            gstin=data.gstin,
            pan=data.pan,
            phone=data.phone,
            email=data.email,
            address=data.address,
            state_code=data.state_code,
            is_active=True,
        )
        self.db.add(ledger)
        self.db.flush()

        self.audit.emit(
            action="ledger.created",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=None,
            new_value=_ledger_snapshot(ledger),
        )
        return ledger

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, ledger_id: UUID) -> Ledger:
        ledger = (
            self.db.query(Ledger)
            .filter(Ledger.id == ledger_id, Ledger.company_id == self.company_id)
            .first()
        )
        if ledger is None:
            raise LedgerNotFound("Ledger not found.")
        return ledger

    def list(
        self,
        *,
        group: str | None,
        is_active: bool | None,
        q: str | None,
        limit: int,
    ) -> tuple[list[Ledger], int]:
        query = self.db.query(Ledger).filter(
            Ledger.company_id == self.company_id
        )
        if is_active is None:
            query = query.filter(Ledger.is_active.is_(True))
        else:
            query = query.filter(Ledger.is_active.is_(is_active))
        if group is not None:
            query = query.filter(Ledger.group_name == group)
        if q:
            term = _normalize(q)
            # gin_trgm_ops on name_normalized makes ILIKE %term% +
            # similarity threshold both indexable; we combine to keep
            # short-prefix lookups fast and also catch typo'd queries.
            query = query.filter(
                or_(
                    Ledger.name_normalized.ilike(f"%{term}%"),
                    func.similarity(Ledger.name_normalized, term) > 0.3,
                )
            ).order_by(
                func.similarity(Ledger.name_normalized, term).desc(),
                Ledger.name_normalized.asc(),
            )
        else:
            query = query.order_by(Ledger.name_normalized.asc())
        total = query.count()
        rows = query.limit(limit).all()
        return rows, total

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, ledger_id: UUID, data: LedgerUpdate) -> Ledger:
        ledger = self.get(ledger_id)
        old = _ledger_snapshot(ledger)

        diff = data.model_dump(exclude_unset=True)
        if "name" in diff and diff["name"] is not None:
            ledger.name = diff["name"]
            ledger.name_normalized = _normalize(diff["name"])
        for k, v in diff.items():
            if k == "name":
                continue
            if k == "balance_type" and v is not None:
                ledger.balance_type = BalanceType(v)
            else:
                setattr(ledger, k, v)
        self.db.flush()
        new = _ledger_snapshot(ledger)

        self.audit.emit(
            action="ledger.updated",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=old,
            new_value=new,
        )
        return ledger

    # ------------------------------------------------------------------
    # Soft-delete
    # ------------------------------------------------------------------

    def soft_delete(self, ledger_id: UUID) -> None:
        ledger = self.get(ledger_id)
        # Block if entries exist. Hard delete forbidden either way.
        # Bypass auto-scope on this count because the audit-log row may
        # already filter on tenant; here we filter explicitly.
        entry_count = (
            self.db.query(LedgerEntry)
            .execution_options(**{SCOPE_BYPASS_OPTION: True})
            .filter(
                LedgerEntry.ledger_id == ledger_id,
                LedgerEntry.company_id == self.company_id,
            )
            .count()
        )
        if entry_count > 0:
            raise LedgerInUse(
                "Ledger has voucher entries; cannot deactivate.",
                details={"entry_count": entry_count},
            )

        if not ledger.is_active:
            return  # idempotent

        old = _ledger_snapshot(ledger)
        ledger.is_active = False
        self.db.flush()
        new = _ledger_snapshot(ledger)
        self.audit.emit(
            action="ledger.updated",
            entity_type="ledger",
            entity_id=ledger.id,
            old_value=old,
            new_value=new,
        )

    # ------------------------------------------------------------------
    # sync_masters ingest (P0.46b)
    # ------------------------------------------------------------------

    def upsert_from_sync(
        self,
        *,
        ledgers: list[dict[str, Any]],
        groups: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Idempotent bulk upsert from a connector `sync_masters` reply.

        GUID-first durable identity (Phase 7B): the match key is
        ``(company_id, tally_master_id)`` — the Tally ledger GUID. Name is
        never the identity and never silently overrides GUID identity.

        Decision per payload row:

        * Case A — GUID matches a local row → update name/group/gstin/is_active.
        * Case B — GUID unseen, no name collision → insert a new row.
        * Case C — GUID unseen, but the same name already holds a *different*
          GUID → never merge, never overwrite, never re-insert → skip + WARN
          (the schema's ``(company_id, name)`` unique key also forbids a
          duplicate-name insert).
        * Case D — GUID already present under a *different* company → hard
          stop (data-integrity violation), raises ``CompanyMappingError``.
        * Case E — missing GUID → skip (not persisted as a Tally master).
        * Case F — ambiguous name fallback → skip (never silent override).

        New rows are inserted with ``opening_balance=0`` / ``balance_type=Dr``;
        updates rewrite name/group_name/gstin/is_active and leave
        ``opening_balance`` alone. ``tally_synced_at`` is stamped on every
        processed row (including idempotent no-ops) and excluded from the
        audit snapshot. Returns a ``{created, updated, skipped}`` dict.
        """
        del groups  # not persisted; see P0.46b

        created = 0
        updated = 0
        skipped = 0

        for raw in ledgers:
            name = raw.get("name") if isinstance(raw, dict) else None
            if not isinstance(name, str) or not name.strip():
                skipped += 1
                continue

            norm = _normalize(name)
            group_name = raw.get("group_name") or None
            gstin = raw.get("gstin") or None
            payload_guid = _clean_guid(raw.get("master_id"))

            # Case E — missing GUID: not persisted as a Tally master.
            if payload_guid is None:
                skipped += 1
                continue

            existing = (
                self.db.query(Ledger)
                .filter(
                    Ledger.company_id == self.company_id,
                    Ledger.tally_master_id == payload_guid,
                )
                .first()
            )

            if existing is not None:
                # Case A — same company + GUID → update.
                old_snap = _ledger_snapshot(existing)
                self._apply_sync_update(
                    existing,
                    name=name,
                    norm=norm,
                    group_name=group_name,
                    gstin=gstin,
                )
                existing.tally_synced_at = datetime.now(UTC)
                self.db.flush()
                new_snap = _ledger_snapshot(existing)
                if new_snap == old_snap:
                    # Idempotent no-op — tally_synced_at advances but is
                    # excluded from the snapshot, so no phantom audit row.
                    continue
                self.audit.emit(
                    action="ledger.updated",
                    entity_type="ledger",
                    entity_id=existing.id,
                    old_value=old_snap,
                    new_value=new_snap,
                )
                updated += 1
                continue

            # No GUID match. Resolve the name-collision space.
            name_row = (
                self.db.query(Ledger)
                .filter(
                    Ledger.company_id == self.company_id,
                    Ledger.name_normalized == norm,
                )
                .first()
            )
            if name_row is not None:
                # Case C / F — same name, different (or absent) GUID. Never
                # merge, never overwrite, never re-insert a duplicate name.
                logger.warning(
                    "ledger sync skipped (name/guid conflict): name=%r "
                    "company_id=%s payload_guid=%s existing_guid=%s",
                    name,
                    self.company_id,
                    payload_guid,
                    name_row.tally_master_id,
                )
                skipped += 1
                continue

            # Case D — cross-company GUID reuse is a data-integrity violation.
            cross = (
                self.db.query(Ledger)
                .execution_options(**{SCOPE_BYPASS_OPTION: True})
                .filter(
                    Ledger.tally_master_id == payload_guid,
                    Ledger.company_id != self.company_id,
                )
                .first()
            )
            if cross is not None:
                raise CompanyMappingError(
                    "ledger GUID already attached to another company: "
                    f"guid={payload_guid} company_id={cross.company_id}"
                )

            # Case B — insert.
            ledger_row = Ledger(
                company_id=self.company_id,
                name=name,
                name_normalized=norm,
                group_name=group_name,
                opening_balance=Decimal("0"),
                balance_type=BalanceType.Dr,
                gstin=gstin,
                is_active=True,
                tally_master_id=payload_guid,
                tally_synced_at=datetime.now(UTC),
            )
            self.db.add(ledger_row)
            self.db.flush()
            self.audit.emit(
                action="ledger.created",
                entity_type="ledger",
                entity_id=ledger_row.id,
                old_value=None,
                new_value=_ledger_snapshot(ledger_row),
            )
            created += 1

        return {"created": created, "updated": updated, "skipped": skipped}

    def _apply_sync_update(
        self,
        ledger: Ledger,
        *,
        name: str,
        norm: str,
        group_name: str | None,
        gstin: str | None,
    ) -> None:
        """Rewrite sync-managed fields on an existing ledger (Case A).

        Updates name (guarded against a ``(company_id, name)`` collision with
        a *different*-GUID row), group_name, gstin, and is_active. Leaves
        ``opening_balance``/``balance_type`` untouched.
        """
        ledger.group_name = group_name
        ledger.gstin = gstin
        ledger.is_active = True
        if norm != ledger.name_normalized:
            clash = (
                self.db.query(Ledger)
                .filter(
                    Ledger.company_id == self.company_id,
                    Ledger.name_normalized == norm,
                    Ledger.id != ledger.id,
                )
                .first()
            )
            if clash is None:
                ledger.name = name
                ledger.name_normalized = norm
            else:
                logger.warning(
                    "ledger rename skipped (name collision): ledger_id=%s "
                    "new_name=%r collides with ledger_id=%s",
                    ledger.id,
                    name,
                    clash.id,
                )
