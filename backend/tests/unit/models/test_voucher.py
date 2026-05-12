"""Unit tests for Voucher and LedgerEntry models."""

from __future__ import annotations

from app.models.base import TenantScopedMixin
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)

# ---------------- Voucher ----------------


def test_voucher_tablename() -> None:
    assert Voucher.__tablename__ == "vouchers"


def test_voucher_is_tenant_scoped() -> None:
    assert issubclass(Voucher, TenantScopedMixin)


def test_voucher_columns_match_schema() -> None:
    cols = {c.name for c in Voucher.__table__.columns}
    assert cols == {
        "id",
        "company_id",
        "voucher_type",
        "voucher_number",
        "date",
        "narration",
        "reference",
        "total_amount",
        "status",
        "source",
        "source_ingestion_id",
        "is_auto_posted",
        "confidence_score",
        "gst_applicable",
        "place_of_supply",
        "cgst",
        "sgst",
        "igst",
        "cess",
        "tds_applicable",
        "tds_amount",
        "tds_section",
        "tally_posted_at",
        "tally_voucher_guid",
        "tally_post_attempts",
        "tally_last_error",
        "is_optional_in_tally",
        "approved_to_regular_at",
        "approved_to_regular_by",
        "optional_rejection_reason",
        "optional_rejected_at",
        "optional_rejected_by",
        "created_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    }


def test_voucher_company_id_restrict_on_company_delete() -> None:
    fk = next(iter(Voucher.__table__.columns["company_id"].foreign_keys))
    assert fk.ondelete == "RESTRICT"


def test_voucher_total_amount_numeric_15_2_required() -> None:
    col = Voucher.__table__.columns["total_amount"]
    assert isinstance(col.type, Numeric)
    assert (col.type.precision, col.type.scale) == (15, 2)
    assert col.nullable is False


def test_voucher_confidence_numeric_4_3_optional() -> None:
    col = Voucher.__table__.columns["confidence_score"]
    assert isinstance(col.type, Numeric)
    assert (col.type.precision, col.type.scale) == (4, 3)
    assert col.nullable is True


def test_voucher_date_required() -> None:
    col = Voucher.__table__.columns["date"]
    assert isinstance(col.type, Date)
    assert col.nullable is False


def test_voucher_narration_is_text() -> None:
    assert isinstance(Voucher.__table__.columns["narration"].type, Text)


def test_voucher_status_default_posted() -> None:
    col = Voucher.__table__.columns["status"]
    assert col.nullable is False
    assert "posted" in str(col.server_default.arg)


def test_voucher_source_default_manual() -> None:
    col = Voucher.__table__.columns["source"]
    assert isinstance(col.type, String)
    assert col.type.length == 20
    assert "manual" in str(col.server_default.arg)


def test_voucher_gst_components_default_zero() -> None:
    for name in ("cgst", "sgst", "igst", "cess"):
        col = Voucher.__table__.columns[name]
        assert isinstance(col.type, Numeric)
        assert (col.type.precision, col.type.scale) == (15, 2)
        assert col.nullable is False


def test_voucher_tally_post_attempts_int_default_zero() -> None:
    col = Voucher.__table__.columns["tally_post_attempts"]
    assert isinstance(col.type, Integer)
    assert col.nullable is False


def test_voucher_boolean_flags() -> None:
    for name in ("is_auto_posted", "gst_applicable", "tds_applicable"):
        col = Voucher.__table__.columns[name]
        assert isinstance(col.type, Boolean)
        assert col.nullable is False


def test_voucher_unique_constraint_is_deferrable() -> None:
    [uc] = [
        c
        for c in Voucher.__table__.constraints
        if c.name == "uq_vouchers_company_number_type"
    ]
    assert uc.deferrable is True
    assert uc.initially == "DEFERRED"


def test_voucher_check_constraints_present() -> None:
    names = {c.name for c in Voucher.__table__.constraints if c.name is not None}
    assert {
        "ck_vouchers_total_positive",
        "ck_vouchers_confidence_range",
        "ck_vouchers_source",
        "ck_vouchers_gst_components",
        "ck_vouchers_tds",
        "ck_vouchers_place_of_supply",
    }.issubset(names)


def test_voucher_indexes_match_schema() -> None:
    names = {ix.name for ix in Voucher.__table__.indexes}
    assert {
        "idx_vouchers_company_date",
        "idx_vouchers_company_status",
        "idx_vouchers_company_type_date",
        "idx_vouchers_source_ingestion",
        "idx_vouchers_unposted_to_tally",
        "idx_vouchers_optional_pending",
    }.issubset(names)


def test_voucher_type_enum_values() -> None:
    assert {v.value for v in VoucherType} == {
        "Receipt",
        "Payment",
        "Sales",
        "Purchase",
        "Journal",
        "Contra",
        "Debit Note",
        "Credit Note",
    }


def test_voucher_status_enum_values() -> None:
    assert {s.value for s in VoucherStatus} == {
        "draft",
        "pending_approval",
        "optional",
        "posted",
        "cancelled",
        "rejected_optional",
    }


def test_voucher_timestamps_have_tz() -> None:
    for name in (
        "created_at",
        "updated_at",
        "tally_posted_at",
        "approved_at",
        "approved_to_regular_at",
        "optional_rejected_at",
    ):
        col = Voucher.__table__.columns[name]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True


def test_voucher_is_optional_in_tally_default_false() -> None:
    col = Voucher.__table__.columns["is_optional_in_tally"]
    assert isinstance(col.type, Boolean)
    assert col.nullable is False
    assert "FALSE" in str(col.server_default.arg).upper()


def test_voucher_optional_reviewer_fks_set_null() -> None:
    for name in ("approved_to_regular_by", "optional_rejected_by"):
        col = Voucher.__table__.columns[name]
        fk = next(iter(col.foreign_keys))
        assert fk.ondelete == "SET NULL"
        assert col.nullable is True


def test_voucher_optional_rejection_reason_is_text() -> None:
    col = Voucher.__table__.columns["optional_rejection_reason"]
    assert isinstance(col.type, Text)
    assert col.nullable is True


# ---------------- LedgerEntry ----------------


def test_ledger_entry_tablename() -> None:
    assert LedgerEntry.__tablename__ == "ledger_entries"


def test_ledger_entry_is_tenant_scoped() -> None:
    assert issubclass(LedgerEntry, TenantScopedMixin)


def test_ledger_entry_columns_match_schema() -> None:
    cols = {c.name for c in LedgerEntry.__table__.columns}
    assert cols == {
        "id",
        "company_id",
        "voucher_id",
        "ledger_id",
        "amount",
        "entry_type",
        "line_number",
        "narration",
        "gst_rate",
        "cgst",
        "sgst",
        "igst",
        "tds_amount",
        "tds_section",
        "created_at",
    }


def test_ledger_entry_voucher_id_cascade_on_voucher_delete() -> None:
    fk = next(iter(LedgerEntry.__table__.columns["voucher_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_ledger_entry_ledger_id_restrict_on_ledger_delete() -> None:
    fk = next(iter(LedgerEntry.__table__.columns["ledger_id"].foreign_keys))
    assert fk.ondelete == "RESTRICT"


def test_ledger_entry_amount_required_numeric_15_2() -> None:
    col = LedgerEntry.__table__.columns["amount"]
    assert isinstance(col.type, Numeric)
    assert (col.type.precision, col.type.scale) == (15, 2)
    assert col.nullable is False


def test_ledger_entry_gst_rate_optional_5_2() -> None:
    col = LedgerEntry.__table__.columns["gst_rate"]
    assert isinstance(col.type, Numeric)
    assert (col.type.precision, col.type.scale) == (5, 2)
    assert col.nullable is True


def test_ledger_entry_constraints() -> None:
    names = {
        c.name for c in LedgerEntry.__table__.constraints if c.name is not None
    }
    assert {
        "uq_ledger_entries_voucher_line",
        "ck_ledger_entries_amount_positive",
        "ck_ledger_entries_gst_rate",
    }.issubset(names)


def test_ledger_entry_indexes() -> None:
    names = {ix.name for ix in LedgerEntry.__table__.indexes}
    assert {
        "idx_ledger_entries_voucher",
        "idx_ledger_entries_ledger",
        "idx_ledger_entries_company_ledger",
    }.issubset(names)


def test_entry_type_enum_values() -> None:
    assert {e.value for e in EntryType} == {"Dr", "Cr"}
