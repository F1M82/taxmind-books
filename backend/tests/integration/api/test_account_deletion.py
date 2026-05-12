"""Integration tests for /api/v1/account/deletion-request (P0.45)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.account_deletion_request import (
    AccountDeletionRequest,
    AccountDeletionStatus,
)
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.services.account_lifecycle_service import GRACE_PERIOD_DAYS
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    issue_token,
    make_company,
    make_membership,
    make_user,
)


def _auth(user) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {issue_token(user)}"}


# ---------------------------------------------------------------------
# create
# ---------------------------------------------------------------------


def test_request_deletion_creates_grace_row_and_audit(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)

    r = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == AccountDeletionStatus.grace_period.value

    row = db_session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id
        )
    )
    assert row is not None
    assert row.status == AccountDeletionStatus.grace_period
    # Grace window is ~30 days in the future.
    delta = row.grace_ends_at - row.requested_at
    assert (
        timedelta(days=GRACE_PERIOD_DAYS - 1)
        < delta
        < timedelta(days=GRACE_PERIOD_DAYS + 1)
    )

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "account.deletion_requested",
            AuditLog.entity_id == row.id,
        )
    )
    assert audit is not None
    assert audit.user_id == user.id
    # DPDP/account lifecycle is a system event, not company-scoped.
    assert audit.company_id is None


def test_request_deletion_is_idempotent_during_grace(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)

    r1 = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    r2 = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={"reason": "second attempt"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r2.json()["id"] == r1.json()["id"]

    rows = (
        db_session.execute(
            select(AccountDeletionRequest).where(
                AccountDeletionRequest.user_id == user.id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_request_deletion_blocks_sole_owner(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    company = make_company(db_session, name="Solo Co")
    make_membership(db_session, user, company, role=CompanyRole.owner)

    r = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"]["code"] == "ownership_transfer_required"
    assert str(company.id) in body["error"]["details"]["companies"]

    # No row written.
    row = db_session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id
        )
    )
    assert row is None


def test_request_deletion_allows_when_another_owner_exists(
    client: TestClient, db_session: Session
) -> None:
    user_a = make_user(db_session, email="a@example.com")
    user_b = make_user(db_session, email="b@example.com")
    company = make_company(db_session, name="Shared Co")
    make_membership(db_session, user_a, company, role=CompanyRole.owner)
    make_membership(db_session, user_b, company, role=CompanyRole.owner)

    r = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user_a),
        json={},
    )
    assert r.status_code == 201, r.text


def test_request_deletion_allows_when_only_membership_is_non_owner(
    client: TestClient, db_session: Session
) -> None:
    owner = make_user(db_session, email="owner@example.com")
    user = make_user(db_session, email="acct@example.com")
    company = make_company(db_session, name="Co")
    make_membership(db_session, owner, company, role=CompanyRole.owner)
    make_membership(db_session, user, company, role=CompanyRole.accountant)

    r = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    assert r.status_code == 201, r.text


def test_request_deletion_requires_auth(client: TestClient) -> None:
    r = client.post("/api/v1/account/deletion-request", json={})
    assert r.status_code == 401


# ---------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------


def test_cancel_during_grace_flips_status_and_audits(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )

    r = client.delete(
        "/api/v1/account/deletion-request", headers=_auth(user)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == AccountDeletionStatus.cancelled.value

    row = db_session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id
        )
    )
    assert row is not None
    assert row.status == AccountDeletionStatus.cancelled
    assert row.cancelled_at is not None

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "account.deletion_cancelled",
            AuditLog.entity_id == row.id,
        )
    )
    assert audit is not None


def test_cancel_404s_when_no_pending_request(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r = client.delete(
        "/api/v1/account/deletion-request", headers=_auth(user)
    )
    assert r.status_code == 404


def test_cancel_404s_after_already_cancelled(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    client.delete(
        "/api/v1/account/deletion-request", headers=_auth(user)
    )

    # No more `grace_period` row, so cancellation no longer applies.
    r = client.delete(
        "/api/v1/account/deletion-request", headers=_auth(user)
    )
    assert r.status_code == 404


def test_re_request_after_cancel_creates_new_grace_row(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    r1 = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    client.delete(
        "/api/v1/account/deletion-request", headers=_auth(user)
    )

    r2 = client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] != r1.json()["id"]

    rows = (
        db_session.execute(
            select(AccountDeletionRequest)
            .where(AccountDeletionRequest.user_id == user.id)
            .order_by(AccountDeletionRequest.requested_at.asc())
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert rows[0].status == AccountDeletionStatus.cancelled
    assert rows[1].status == AccountDeletionStatus.grace_period


def test_grace_window_uses_utc(
    client: TestClient, db_session: Session
) -> None:
    user = make_user(db_session)
    before = datetime.now(UTC)
    client.post(
        "/api/v1/account/deletion-request",
        headers=_auth(user),
        json={},
    )
    after = datetime.now(UTC)

    row = db_session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.user_id == user.id
        )
    )
    assert row is not None
    assert before <= row.requested_at <= after
    assert row.grace_ends_at.tzinfo is not None
