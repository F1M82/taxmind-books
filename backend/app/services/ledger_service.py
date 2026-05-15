"""Ledger service: CRUD + fuzzy search (P0.17) + sync_masters ingest (P0.46b)."""

from __future__ import annotations

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
            existing.group_name = group_name
            existing.gstin = gstin
            existing.is_active = True
            self.db.flush()
            new_snap = _ledger_snapshot(existing)
            if new_snap == old_snap:
                # Re-sync of identical data — idempotent no-op, no audit row.
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
