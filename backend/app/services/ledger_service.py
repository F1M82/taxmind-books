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

logger = logging.getLogger("app.services.ledger_service")


def _normalize(name: str) -> str:
    return name.strip().lower()


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

        Match key is `(company_id, name_normalized)`. New rows are inserted
        with the model's default `opening_balance=0` and `balance_type=Dr`;
        existing rows have `group_name`, `gstin`, and `is_active=True`
        rewritten in place. `opening_balance` is left alone on update so
        a manually-edited balance is not clobbered by a re-sync.

        `groups` is accepted to mirror the connector payload contract but
        is not persisted — the schema denormalizes group identity into
        `ledgers.group_name` (see P0.46b in PHASE_0_TASKS.md).

        Tally GUID reconciliation (BUG-005): the payload may include
        per-ledger `master_id`. On insert, it's written verbatim. On
        update, the four-case matrix applies — local NULL + payload GUID
        writes (main reconciliation path), local GUID + payload NULL is
        ignored, GUID mismatch on same name logs WARN and holds the local
        value (Tally GUIDs should be stable). `tally_synced_at` is
        stamped on every successful per-row processing — including
        idempotent no-ops — and is excluded from the audit snapshot so
        it never triggers a phantom audit row.

        Returns a `{created, updated, skipped}` count dict.
        """
        del groups  # not persisted in Phase 0; see docstring

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
            payload_guid = raw.get("master_id") or None

            existing = (
                self.db.query(Ledger)
                .filter(
                    Ledger.company_id == self.company_id,
                    Ledger.name_normalized == norm,
                )
                .first()
            )

            if existing is None:
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
                continue

            old_snap = _ledger_snapshot(existing)

            # Reconcile matrix per BUG-005 decision:
            #   local NULL + payload GUID → write (main reconciliation path)
            #   local GUID + payload NULL → ignore (don't clobber known-good)
            #   local GUID != payload GUID → log WARN, hold local
            #     (Tally GUIDs should be stable — mismatch is an anomaly
            #     needing operator investigation, not silent overwrite)
            #   both NULL, or both equal → no-op (falls through)
            local_guid = existing.tally_master_id
            if local_guid is None and payload_guid is not None:
                existing.tally_master_id = payload_guid
            elif local_guid is not None and payload_guid is None:
                pass  # don't clobber known-good local GUID
            elif (
                local_guid is not None
                and payload_guid is not None
                and local_guid != payload_guid
            ):
                logger.warning(
                    "ledger reconciliation skipped: name=%r company_id=%s "
                    "local_guid=%s payload_guid=%s (Tally GUIDs should be "
                    "stable; investigate before manual reconciliation)",
                    name,
                    self.company_id,
                    local_guid,
                    payload_guid,
                )

            existing.group_name = group_name
            existing.gstin = gstin
            existing.is_active = True
            existing.tally_synced_at = datetime.now(UTC)
            self.db.flush()
            new_snap = _ledger_snapshot(existing)
            if new_snap == old_snap:
                # Re-sync of identical data — idempotent no-op, no audit row.
                # tally_synced_at was stamped above but is excluded from the
                # snapshot, so it doesn't trigger a phantom audit row.
                continue
            self.audit.emit(
                action="ledger.updated",
                entity_type="ledger",
                entity_id=existing.id,
                old_value=old_snap,
                new_value=new_snap,
            )
            updated += 1

        return {"created": created, "updated": updated, "skipped": skipped}
