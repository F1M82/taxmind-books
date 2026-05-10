"""Voucher service: create (P0.18). Read/update/cancel land in P0.19."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.audit import AuditEmitter
from app.core.exceptions import (
    LedgerNotFound,
    ValidationFailed,
    VoucherEntriesUnbalanced,
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
from app.schemas.voucher import VoucherCreate


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
        self._validate_ledger_ownership(data)

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

    def _validate_ledger_ownership(self, data: VoucherCreate) -> None:
        ledger_ids = {e.ledger_id for e in data.entries}
        owned_ids: set[UUID] = {
            row.id
            for row in self.db.query(Ledger.id)
            .filter(
                Ledger.id.in_(ledger_ids),
                Ledger.company_id == self.company_id,
            )
            .all()
        }
        missing = ledger_ids - owned_ids
        if missing:
            raise LedgerNotFound(
                "One or more ledgers do not belong to the active company.",
                details={"missing_ledger_ids": [str(i) for i in missing]},
            )
