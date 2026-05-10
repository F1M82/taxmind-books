"""Unit tests for the Ledger model."""

from __future__ import annotations

from app.models.ledger import BalanceType, Ledger
from sqlalchemy import Boolean, DateTime, Numeric, String, Text


def test_ledger_tablename() -> None:
    assert Ledger.__tablename__ == "ledgers"


def test_ledger_has_expected_columns() -> None:
    cols = {c.name for c in Ledger.__table__.columns}
    assert cols == {
        "id",
        "company_id",
        "name",
        "name_normalized",
        "group_name",
        "parent_ledger_id",
        "opening_balance",
        "balance_type",
        "gstin",
        "pan",
        "phone",
        "email",
        "address",
        "state_code",
        "is_active",
        "tally_master_id",
        "tally_synced_at",
        "created_at",
        "updated_at",
    }


def test_ledger_company_id_restrict_on_company_delete() -> None:
    fk = next(iter(Ledger.__table__.columns["company_id"].foreign_keys))
    assert fk.ondelete == "RESTRICT"
    assert fk.column.table.name == "companies"


def test_ledger_parent_ledger_id_set_null_on_delete() -> None:
    fk = next(iter(Ledger.__table__.columns["parent_ledger_id"].foreign_keys))
    assert fk.ondelete == "SET NULL"
    assert fk.column.table.name == "ledgers"


def test_ledger_name_required() -> None:
    col = Ledger.__table__.columns["name"]
    assert isinstance(col.type, String)
    assert col.type.length == 255
    assert col.nullable is False


def test_ledger_name_normalized_required() -> None:
    col = Ledger.__table__.columns["name_normalized"]
    assert col.nullable is False


def test_ledger_address_is_text() -> None:
    assert isinstance(Ledger.__table__.columns["address"].type, Text)


def test_ledger_opening_balance_is_numeric_15_2() -> None:
    col = Ledger.__table__.columns["opening_balance"]
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 15
    assert col.type.scale == 2
    assert col.nullable is False


def test_ledger_is_active_default_true() -> None:
    col = Ledger.__table__.columns["is_active"]
    assert isinstance(col.type, Boolean)
    assert col.nullable is False
    assert "TRUE" in str(col.server_default.arg).upper()


def test_ledger_balance_type_default_dr() -> None:
    col = Ledger.__table__.columns["balance_type"]
    assert col.nullable is False
    assert "Dr" in str(col.server_default.arg)


def test_ledger_timestamp_columns_have_tz() -> None:
    for name in ("created_at", "updated_at", "tally_synced_at"):
        col = Ledger.__table__.columns[name]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True


def test_ledger_constraints_match_schema() -> None:
    names = {c.name for c in Ledger.__table__.constraints if c.name is not None}
    assert {
        "uq_ledgers_company_name",
        "uq_ledgers_company_tally",
        "ck_ledgers_gstin_format",
        "ck_ledgers_pan_format",
    }.issubset(names)


def test_ledger_indexes_match_schema() -> None:
    names = {ix.name for ix in Ledger.__table__.indexes}
    assert {
        "idx_ledgers_company",
        "idx_ledgers_company_active",
        "idx_ledgers_company_group",
        "idx_ledgers_gstin",
        "idx_ledgers_pan",
        "idx_ledgers_name_trgm",
    }.issubset(names)


def test_ledger_balance_type_enum_values() -> None:
    assert {b.value for b in BalanceType} == {"Dr", "Cr"}


def test_ledger_is_tenant_scoped() -> None:
    from app.models.base import TenantScopedMixin

    assert issubclass(Ledger, TenantScopedMixin)
