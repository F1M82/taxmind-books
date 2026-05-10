"""Unit tests for Company and UserCompany models."""

from __future__ import annotations

from app.models.company import (
    Company,
    CompanyRole,
    CompanyStatus,
    UserCompany,
)
from sqlalchemy import Date, DateTime, String, Text

# ---------------- Company ----------------


def test_company_tablename() -> None:
    assert Company.__tablename__ == "companies"


def test_company_has_expected_columns() -> None:
    cols = {c.name for c in Company.__table__.columns}
    assert cols == {
        "id",
        "name",
        "gstin",
        "pan",
        "financial_year_start",
        "accounting_source",
        "status",
        "address",
        "city",
        "state_code",
        "pincode",
        "created_by",
        "created_at",
        "updated_at",
    }


def test_company_name_is_required() -> None:
    col = Company.__table__.columns["name"]
    assert isinstance(col.type, String)
    assert col.type.length == 255
    assert col.nullable is False


def test_company_address_is_text() -> None:
    col = Company.__table__.columns["address"]
    assert isinstance(col.type, Text)
    assert col.nullable is True


def test_company_financial_year_start_is_date_required() -> None:
    col = Company.__table__.columns["financial_year_start"]
    assert isinstance(col.type, Date)
    assert col.nullable is False


def test_company_timestamp_columns_have_tz() -> None:
    for name in ("created_at", "updated_at"):
        col = Company.__table__.columns[name]
        assert isinstance(col.type, DateTime)
        assert col.type.timezone is True


def test_company_constraints_match_schema() -> None:
    names = {c.name for c in Company.__table__.constraints if c.name is not None}
    expected = {
        "uq_companies_gstin",
        "ck_companies_gstin_format",
        "ck_companies_pan_format",
        "ck_companies_pincode_format",
        "ck_companies_state_code_format",
        "ck_companies_fy_start_april",
        "ck_companies_accounting_source",
    }
    assert expected.issubset(names)


def test_company_indexes() -> None:
    names = {ix.name for ix in Company.__table__.indexes}
    assert {"idx_companies_status", "idx_companies_gstin"}.issubset(names)


def test_company_status_enum_values() -> None:
    assert {s.value for s in CompanyStatus} == {"active", "inactive", "suspended"}


# ---------------- UserCompany ----------------


def test_user_company_tablename() -> None:
    assert UserCompany.__tablename__ == "user_companies"


def test_user_company_has_expected_columns() -> None:
    cols = {c.name for c in UserCompany.__table__.columns}
    assert cols == {
        "id",
        "user_id",
        "company_id",
        "role",
        "created_at",
        "updated_at",
    }


def test_user_company_user_id_cascade_on_user_delete() -> None:
    fk = next(iter(UserCompany.__table__.columns["user_id"].foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_user_company_company_id_restrict_on_company_delete() -> None:
    fk = next(iter(UserCompany.__table__.columns["company_id"].foreign_keys))
    assert fk.ondelete == "RESTRICT"


def test_user_company_unique_constraint() -> None:
    names = {c.name for c in UserCompany.__table__.constraints if c.name is not None}
    assert "uq_user_companies_user_company" in names


def test_user_company_role_enum_values() -> None:
    assert {r.value for r in CompanyRole} == {"owner", "admin", "accountant", "viewer"}


def test_user_company_indexes() -> None:
    names = {ix.name for ix in UserCompany.__table__.indexes}
    assert "idx_user_companies_user" in names
    assert "idx_user_companies_company" in names
