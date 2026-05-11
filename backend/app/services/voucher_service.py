"""Voucher service: create (P0.18). Read/update/cancel land in P0.19."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import (
    LedgerNotFound,
    ValidationFailed,
    VoucherAlreadyCancelled,
    VoucherEntriesUnbalanced,
    VoucherImmutableField,
    VoucherNotFound,
    VoucherTypeRuleViolation,
)
from app.models.ledger import Ledger
from app.models.user import User
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.schemas.voucher import VoucherCreate, VoucherUpdate
from app.services.voucher_groups import (
    is_bank_or_cash,
    is_sundry_creditors,
    is_sundry_debtors,
)


def _voucher_snapshot(v: Voucher) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "company_id": str(v.company_id),
        "voucher_type": v.voucher_type.value
        if hasattr(v.voucher_type, "value")
        else str(v.voucher_type),
        "voucher_number": v.voucher_number,
        "date": v.date.isoformat(),
        "narration": v.narration,
        "reference": v.reference,
        "total_amount": str(v.total_amount),
        "status": v.status.value if hasattr(v.status, "value") else str(v.status),
        "source": v.source,
        "is_auto_posted": v.is_auto_posted,
        "confidence_score": (
            str(v.confidence_score) if v.confidence_score is not None else None
        ),
        "gst_applicable": v.gst_applicable,
        "place_of_supply": v.place_of_supply,
        "cgst": str(v.cgst),
        "sgst": str(v.sgst),
        "igst": str(v.igst),
        "cess": str(v.cess),
        "tds_applicable": v.tds_applicable,
        "tds_amount": str(v.tds_amount),
        "tds_section": v.tds_section,
        "entries": [
            {
                "ledger_id": str(e.ledger_id),
                "amount": str(e.amount),
                "entry_type": e.entry_type.value
                if hasattr(e.entry_type, "value")
                else str(e.entry_type),
                "line_number": e.line_number,
                "narration": e.narration,
            }
            for e in v.entries
        ],
    }


class VoucherService:
    def __init__(
        self, db: Session, audit: AuditEmitter, company_id: UUID
    ) -> None:
        self.db = db
        self.audit = audit
        self.company_id = company_id

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(self, data: VoucherCreate, actor: User) -> Voucher:
        self._validate_entries(data)
        self._validate_gst(data)
        ledger_groups = self._validate_ledger_ownership(data)
        self._validate_type_rules(data, ledger_groups)

        voucher = Voucher(
            company_id=self.company_id,
            voucher_type=VoucherType(data.voucher_type),
            voucher_number=data.voucher_number,
            date=data.date,
            narration=data.narration,
            reference=data.reference,
            total_amount=data.total_amount,
            status=VoucherStatus.posted,
            source="manual",
            is_auto_posted=False,
            gst_applicable=data.gst_applicable,
            place_of_supply=data.place_of_supply,
            cgst=data.cgst,
            sgst=data.sgst,
            igst=data.igst,
            cess=data.cess,
            tds_applicable=data.tds_applicable,
            tds_amount=data.tds_amount,
            tds_section=data.tds_section,
            created_by=actor.id,
        )
        self.db.add(voucher)
        self.db.flush()  # populate voucher.id

        for idx, entry_data in enumerate(data.entries, start=1):
            entry = LedgerEntry(
                company_id=self.company_id,
                voucher_id=voucher.id,
                ledger_id=entry_data.ledger_id,
                amount=entry_data.amount,
                entry_type=EntryType(entry_data.entry_type),
                line_number=idx,
                narration=entry_data.narration,
                gst_rate=entry_data.gst_rate,
                cgst=entry_data.cgst,
                sgst=entry_data.sgst,
                igst=entry_data.igst,
                tds_amount=entry_data.tds_amount,
                tds_section=entry_data.tds_section,
            )
            self.db.add(entry)
        self.db.flush()

        self.audit.emit(
            action="voucher.created",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=None,
            new_value=_voucher_snapshot(voucher),
        )
        return voucher

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_entries(self, data: VoucherCreate) -> None:
        dr_total = sum(
            (e.amount for e in data.entries if e.entry_type == "Dr"),
            start=Decimal("0"),
        )
        cr_total = sum(
            (e.amount for e in data.entries if e.entry_type == "Cr"),
            start=Decimal("0"),
        )
        if dr_total != cr_total:
            raise VoucherEntriesUnbalanced(
                "Dr total must equal Cr total.",
                details={"dr_total": str(dr_total), "cr_total": str(cr_total)},
            )
        if dr_total != data.total_amount:
            raise VoucherEntriesUnbalanced(
                "total_amount must equal sum of Dr entries.",
                details={
                    "total_amount": str(data.total_amount),
                    "dr_total": str(dr_total),
                },
            )

    def _validate_gst(self, data: VoucherCreate) -> None:
        if data.gst_applicable and data.place_of_supply is None:
            raise ValidationFailed(
                "place_of_supply is required when gst_applicable is true.",
                details={"field": "place_of_supply"},
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, voucher_id: UUID) -> Voucher:
        voucher = (
            self.db.query(Voucher)
            .filter(
                Voucher.id == voucher_id,
                Voucher.company_id == self.company_id,
            )
            .first()
        )
        if voucher is None:
            raise VoucherNotFound("Voucher not found.")
        return voucher

    def list(
        self,
        *,
        voucher_type: str | None,
        date_from,  # type: ignore[no-untyped-def]
        date_to,  # type: ignore[no-untyped-def]
        status_filter: str | None,
        ledger_id: UUID | None,
        source: str | None,
        limit: int,
    ) -> tuple[list[Voucher], int]:
        from sqlalchemy import select

        q = self.db.query(Voucher).filter(
            Voucher.company_id == self.company_id
        )
        if voucher_type:
            q = q.filter(
                Voucher.voucher_type == VoucherType(voucher_type)
            )
        if date_from is not None:
            q = q.filter(Voucher.date >= date_from)
        if date_to is not None:
            q = q.filter(Voucher.date <= date_to)
        if status_filter:
            q = q.filter(Voucher.status == VoucherStatus(status_filter))
        if source:
            q = q.filter(Voucher.source == source)
        if ledger_id is not None:
            sub = (
                select(LedgerEntry.voucher_id)
                .where(
                    LedgerEntry.ledger_id == ledger_id,
                    LedgerEntry.company_id == self.company_id,
                )
                .scalar_subquery()
            )
            q = q.filter(Voucher.id.in_(sub))
        total = q.count()
        rows = (
            q.order_by(Voucher.date.desc(), Voucher.created_at.desc())
            .limit(limit)
            .all()
        )
        return rows, total

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    # Mutable fields per API.md.
    _MUTABLE_FIELDS = frozenset({"narration", "reference"})

    def update(
        self, voucher_id: UUID, data: VoucherUpdate, raw_keys: set[str]
    ) -> Voucher:
        """Apply a PATCH. `raw_keys` is the set of fields the client
        actually sent (model_dump(exclude_unset=True).keys()) so we
        can fail loudly on attempts to modify immutable fields.
        """
        offending = raw_keys - self._MUTABLE_FIELDS
        if offending:
            raise VoucherImmutableField(
                "Voucher field is not mutable post-creation.",
                details={"fields": sorted(offending)},
            )

        voucher = self.get(voucher_id)
        if voucher.status == VoucherStatus.cancelled:
            raise VoucherAlreadyCancelled(
                "Cannot modify a cancelled voucher.",
            )

        old = _voucher_snapshot(voucher)
        for k in raw_keys:
            setattr(voucher, k, getattr(data, k))
        self.db.flush()
        new = _voucher_snapshot(voucher)
        self.audit.emit(
            action="voucher.updated",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=old,
            new_value=new,
        )
        return voucher

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel(self, voucher_id: UUID, reason: str) -> Voucher:
        voucher = self.get(voucher_id)
        if voucher.status == VoucherStatus.cancelled:
            raise VoucherAlreadyCancelled(
                "Voucher already cancelled.",
            )
        old = _voucher_snapshot(voucher)
        voucher.status = VoucherStatus.cancelled
        self.db.flush()
        new = _voucher_snapshot(voucher)
        # `reason` is included in the audit row's new_value but not on
        # the voucher itself — Phase-0 model has no cancel_reason col.
        new["cancel_reason"] = reason
        self.audit.emit(
            action="voucher.cancelled",
            entity_type="voucher",
            entity_id=voucher.id,
            old_value=old,
            new_value=new,
        )
        return voucher

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_ledger_ownership(
        self, data: VoucherCreate
    ) -> dict[UUID, str | None]:
        """Confirm every entry's ledger belongs to the active company.

        Returns a `{ledger_id: group_name}` map so the caller can run
        type-specific rules (Sales requires Sundry Debtors, etc.) without
        a second query.
        """
        ledger_ids = {e.ledger_id for e in data.entries}
        rows = (
            self.db.query(Ledger.id, Ledger.group_name)
            .filter(
                Ledger.id.in_(ledger_ids),
                Ledger.company_id == self.company_id,
            )
            .all()
        )
        groups: dict[UUID, str | None] = {row.id: row.group_name for row in rows}
        missing = ledger_ids - set(groups.keys())
        if missing:
            raise LedgerNotFound(
                "One or more ledgers do not belong to the active company.",
                details={"missing_ledger_ids": [str(i) for i in missing]},
            )
        return groups

    # ------------------------------------------------------------------
    # Per-type business rules (P0.36)
    # ------------------------------------------------------------------
    #
    # Per docs/API.md § "POST /vouchers/" → Validation rules. The catalog
    # below is exhaustive for the 8 Tally voucher types; rules trip 422
    # `voucher_type_rule_violation` so clients can route the message to
    # the right form field.

    def _validate_type_rules(
        self, data: VoucherCreate, ledger_groups: dict[UUID, str | None]
    ) -> None:
        validator = _TYPE_VALIDATORS.get(data.voucher_type)
        if validator is None:
            return  # schema-validated enum already excludes unknown types
        entry_groups = [
            (e.entry_type, ledger_groups.get(e.ledger_id)) for e in data.entries
        ]
        validator(data, ledger_groups, entry_groups)


# ---------------------------------------------------------------------
# Per-voucher-type rule functions (P0.36)
# ---------------------------------------------------------------------
#
# Each takes (request, {ledger_id: group_name}, [(entry_type, group_name)])
# and either returns or raises VoucherTypeRuleViolation. Module-level so
# the dispatch dict below stays simple and testable in isolation.

_RuleFn = Callable[
    [VoucherCreate, dict[UUID, str | None], list[tuple[str, str | None]]],
    None,
]


def _rule_sales(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(is_sundry_debtors(g) for _t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Sales voucher must include at least one Sundry Debtors "
            "ledger entry.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_sundry_debtors",
            },
        )


def _rule_purchase(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(is_sundry_creditors(g) for _t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Purchase voucher must include at least one Sundry Creditors "
            "ledger entry.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_sundry_creditors",
            },
        )


def _rule_contra(
    data: VoucherCreate,
    ledger_groups: dict[UUID, str | None],
    _entry_groups: list[tuple[str, str | None]],
) -> None:
    offending = [
        str(e.ledger_id)
        for e in data.entries
        if not is_bank_or_cash(ledger_groups.get(e.ledger_id))
    ]
    if offending:
        raise VoucherTypeRuleViolation(
            "Contra voucher entries must all be on Bank or Cash ledgers.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "all_bank_or_cash",
                "offending_ledger_ids": offending,
            },
        )


def _rule_receipt(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(t == "Dr" and is_bank_or_cash(g) for t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Receipt voucher must debit at least one Bank or Cash ledger.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_bank_or_cash_dr",
            },
        )


def _rule_payment(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(t == "Cr" and is_bank_or_cash(g) for t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Payment voucher must credit at least one Bank or Cash ledger.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_bank_or_cash_cr",
            },
        )


def _rule_journal(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if any(is_bank_or_cash(g) for _t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Journal voucher must not touch Bank or Cash ledgers; "
            "use Receipt, Payment, or Contra instead.",
            details={
                "voucher_type": data.voucher_type,
                "rule": "no_bank_or_cash",
            },
        )


def _rule_debit_note(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(is_sundry_creditors(g) for _t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Debit Note must include at least one Sundry Creditors "
            "ledger entry (purchase return).",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_sundry_creditors",
            },
        )


def _rule_credit_note(
    data: VoucherCreate,
    _ledger_groups: dict[UUID, str | None],
    entry_groups: list[tuple[str, str | None]],
) -> None:
    if not any(is_sundry_debtors(g) for _t, g in entry_groups):
        raise VoucherTypeRuleViolation(
            "Credit Note must include at least one Sundry Debtors "
            "ledger entry (sales return).",
            details={
                "voucher_type": data.voucher_type,
                "rule": "requires_sundry_debtors",
            },
        )


_TYPE_VALIDATORS: dict[str, _RuleFn] = {
    "Sales": _rule_sales,
    "Purchase": _rule_purchase,
    "Contra": _rule_contra,
    "Receipt": _rule_receipt,
    "Payment": _rule_payment,
    "Journal": _rule_journal,
    "Debit Note": _rule_debit_note,
    "Credit Note": _rule_credit_note,
}
