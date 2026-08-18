"""P3.7 Phase 7B — company Tally GUID identity + mapping service tests.

Covers the durable GUID column, the fail-closed decision matrix
(SAFE / MANUAL_REVIEW / AMBIGUOUS / BLOCKED / CONFLICT), operator
confirmation, and the cross-company / no-arbitrary-selection guards.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.audit import AuditContext, AuditEmitter
from app.models.audit_log import AuditLog
from app.models.company import Company, CompanyRole
from app.services.tally.company_mapping import (
    CompanyMappingError,
    CompanyMappingStatus,
    assert_company_mapping_safe,
    confirm_company_mapping,
    require_safe_company_mapping,
    resolve_company_mapping,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company, make_membership, make_user

GUID = "c30a0ee5-4fc5-4fdc-a10e-bd489d5423b9"
OTHER_GUID = "d41b0ee6-5fc5-4fdc-a10e-bd489d5423b9"


def _audit(db: Session, company: Company, user) -> AuditEmitter:  # type: ignore[no-untyped-def]
    return AuditEmitter(
        db,
        AuditContext(
            company=company,
            user=user,
            ip_address=None,
            user_agent="test/1.0",
            request_id=uuid4(),
            source="connector",
        ),
    )


# ---------------------------------------------------------------------
# Company identity (model / DB)
# ---------------------------------------------------------------------


def test_company_stores_tally_guid(db_session: Session) -> None:
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    db_session.expire_all()
    loaded = db_session.query(Company).filter(Company.id == company.id).one()
    assert loaded.tally_master_id == GUID


def test_legacy_company_null_guid_remains_valid(db_session: Session) -> None:
    # Multiple NULL GUIDs must coexist (PostgreSQL treats NULLs as distinct).
    make_company(db_session, name="A")
    make_company(db_session, name="B")
    rows = db_session.query(Company).order_by(Company.name).all()
    assert [r.tally_master_id for r in rows] == [None, None]


def test_migration_does_not_fabricate_guids(db_session: Session) -> None:
    # A company created without a GUID keeps NULL — nothing is invented.
    company = make_company(db_session, name="No Guid Co")
    assert company.tally_master_id is None


def test_duplicate_tally_company_guid_is_rejected(db_session: Session) -> None:
    make_company(db_session, name="Acme", tally_master_id=GUID)
    with pytest.raises(IntegrityError):
        dup = Company(name="Beta", tally_master_id=GUID)
        db_session.add(dup)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------
# Decision matrix
# ---------------------------------------------------------------------


def test_resolve_exact_guid_match_is_safe(db_session: Session) -> None:
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    result = resolve_company_mapping(
        db_session, tally_company_guid=GUID, tally_company_name="Anything"
    )
    assert result.status == CompanyMappingStatus.SAFE
    assert result.mapped is True
    assert result.company_id == company.id


def test_resolve_no_guid_single_name_is_manual_review(db_session: Session) -> None:
    company = make_company(db_session, name="Acme")  # no GUID yet
    result = resolve_company_mapping(
        db_session, tally_company_guid=GUID, tally_company_name="Acme"
    )
    assert result.status == CompanyMappingStatus.MANUAL_REVIEW
    assert result.mapped is False
    assert result.candidate_company_id == company.id


def test_resolve_no_guid_zero_names_is_blocked(db_session: Session) -> None:
    result = resolve_company_mapping(
        db_session, tally_company_guid=GUID, tally_company_name="Missing Co"
    )
    assert result.status == CompanyMappingStatus.BLOCKED


def test_resolve_no_guid_multiple_names_is_ambiguous(db_session: Session) -> None:
    make_company(db_session, name="Dup Co")
    make_company(db_session, name="Dup Co")
    result = resolve_company_mapping(
        db_session, tally_company_guid=GUID, tally_company_name="Dup Co"
    )
    assert result.status == CompanyMappingStatus.AMBIGUOUS
    assert len(result.candidate_company_ids) == 2


def test_resolve_guid_name_conflict(db_session: Session) -> None:
    # Name points at a company already bound to a *different* GUID.
    make_company(db_session, name="Acme", tally_master_id=GUID)
    result = resolve_company_mapping(
        db_session, tally_company_guid=OTHER_GUID, tally_company_name="Acme"
    )
    assert result.status == CompanyMappingStatus.CONFLICT


# ---------------------------------------------------------------------
# Operator confirmation
# ---------------------------------------------------------------------


def test_confirm_binds_guid_and_audits(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    confirmed = confirm_company_mapping(
        db_session,
        company_id=company.id,
        tally_company_guid=GUID,
        tally_company_name="Acme (FROM 1-APR-2025)",
        audit=_audit(db_session, company, user),
    )
    db_session.commit()

    assert confirmed.tally_master_id == GUID
    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.company_id == company.id,
            AuditLog.action == "company.tally_mapping_configured",
        )
        .one()
    )
    assert audit.new_value["tally_master_id"] == GUID


def test_confirm_is_idempotent(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    make_membership(db_session, user, company, role=CompanyRole.owner)

    confirm_company_mapping(
        db_session,
        company_id=company.id,
        tally_company_guid=GUID,
        audit=_audit(db_session, company, user),
    )
    db_session.commit()

    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "company.tally_mapping_configured")
        .count()
        == 0
    )  # no-op, no audit


def test_confirm_rejects_different_existing_guid(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    make_membership(db_session, user, company, role=CompanyRole.owner)

    with pytest.raises(CompanyMappingError):
        confirm_company_mapping(
            db_session,
            company_id=company.id,
            tally_company_guid=OTHER_GUID,
            audit=_audit(db_session, company, user),
        )
    db_session.rollback()
    db_session.expire_all()
    assert company.tally_master_id == GUID  # unchanged


def test_confirm_rejects_guid_already_bound_elsewhere(db_session: Session) -> None:
    # Cross-company GUID reuse is a hard stop.
    user = make_user(db_session)
    other = make_company(db_session, name="Other", tally_master_id=GUID)
    target = make_company(db_session, name="Target")
    make_membership(db_session, user, target, role=CompanyRole.owner)

    with pytest.raises(CompanyMappingError):
        confirm_company_mapping(
            db_session,
            company_id=target.id,
            tally_company_guid=GUID,
            audit=_audit(db_session, target, user),
        )
    db_session.rollback()
    assert other.tally_master_id == GUID


def test_confirm_requires_guid(db_session: Session) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Acme")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    with pytest.raises(CompanyMappingError):
        confirm_company_mapping(
            db_session,
            company_id=company.id,
            tally_company_guid="",
            audit=_audit(db_session, company, user),
        )


# ---------------------------------------------------------------------
# Gates: no arbitrary selection, cross-company, persistence gate
# ---------------------------------------------------------------------


def test_assert_mapping_safe_rejects_arbitrary_company(db_session: Session) -> None:
    company = make_company(db_session, name="NonMatching")
    with pytest.raises(CompanyMappingError):
        assert_company_mapping_safe(
            db_session,
            company_id=company.id,
            tally_company_name="Vighnaharta Agro Chemicals",
        )


def test_assert_mapping_safe_rejects_mismatched_company(db_session: Session) -> None:
    target = make_company(db_session, name="Target", tally_master_id=GUID)
    other = make_company(db_session, name="Other")
    with pytest.raises(CompanyMappingError):
        assert_company_mapping_safe(
            db_session,
            company_id=other.id,
            tally_company_guid=GUID,
        )
    assert target.tally_master_id == GUID


def test_require_safe_company_mapping_returns_company_when_mapped(
    db_session: Session,
) -> None:
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    got = require_safe_company_mapping(
        db_session, company_id=company.id, tally_company_guid=GUID
    )
    assert got.id == company.id


def test_require_safe_company_mapping_blocks_when_unmapped(
    db_session: Session,
) -> None:
    company = make_company(db_session, name="Acme")
    with pytest.raises(CompanyMappingError):
        require_safe_company_mapping(
            db_session, company_id=company.id, tally_company_guid=GUID
        )


def test_require_safe_company_mapping_blocks_on_mismatch(
    db_session: Session,
) -> None:
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    with pytest.raises(CompanyMappingError):
        require_safe_company_mapping(
            db_session, company_id=company.id, tally_company_guid=OTHER_GUID
        )


def test_require_safe_company_mapping_blocks_when_no_guid(
    db_session: Session,
) -> None:
    company = make_company(db_session, name="Acme", tally_master_id=GUID)
    with pytest.raises(CompanyMappingError):
        require_safe_company_mapping(
            db_session, company_id=company.id, tally_company_guid=None
        )
