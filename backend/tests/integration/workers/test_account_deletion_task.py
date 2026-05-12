"""Integration tests for the daily deletion-processing task (P0.45).

Drives `account_lifecycle_service.process_due_deletions` (and the
Celery task that wraps it) against a real Postgres so the cascade
behaviour matches production: hard-deleting the user must remove
memberships, push tokens, and the deletion request itself while
preserving (and anonymising) audit logs and vouchers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.audit import AuditContext, AuditEmitter
from app.models.account_deletion_request import (
    AccountDeletionRequest,
    AccountDeletionStatus,
)
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole, UserCompany
from app.models.device_token import DevicePlatform, DeviceToken
from app.models.user import User
from app.services import account_lifecycle_service
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests._db_fixtures import (
    make_company,
    make_membership,
    make_user,
)


def _worker_audit(db: Session) -> AuditEmitter:
    return AuditEmitter(
        db,
        AuditContext(
            company=None,
            user=None,
            ip_address=None,
            user_agent=None,
            request_id=uuid4(),
            source="worker",
        ),
    )


def _seed_grace_expired_request(
    db: Session, *, user: User
) -> AccountDeletionRequest:
    """Insert a deletion-request row whose grace window already passed."""
    now = datetime.now(UTC)
    row = AccountDeletionRequest(
        user_id=user.id,
        requested_at=now - timedelta(days=31),
        grace_ends_at=now - timedelta(seconds=1),
        status=AccountDeletionStatus.grace_period,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_find_due_returns_only_grace_period_rows_past_cutoff(
    db_session: Session,
) -> None:
    u1 = make_user(db_session, email="due@example.com")
    u2 = make_user(db_session, email="future@example.com")
    u3 = make_user(db_session, email="cancelled@example.com")

    # u1: past-due grace_period (should be returned)
    due = _seed_grace_expired_request(db_session, user=u1)

    # u2: still in grace_period, in the future
    now = datetime.now(UTC)
    db_session.add(
        AccountDeletionRequest(
            user_id=u2.id,
            requested_at=now,
            grace_ends_at=now + timedelta(days=5),
            status=AccountDeletionStatus.grace_period,
        )
    )
    # u3: past-due, but already cancelled
    db_session.add(
        AccountDeletionRequest(
            user_id=u3.id,
            requested_at=now - timedelta(days=31),
            grace_ends_at=now - timedelta(days=1),
            status=AccountDeletionStatus.cancelled,
            cancelled_at=now - timedelta(days=2),
        )
    )
    db_session.commit()

    ids = account_lifecycle_service.find_due_requests(db_session)
    assert ids == [due.id]


def test_process_due_anonymises_user_and_drops_ancillaries(
    db_session: Session,
) -> None:
    user = make_user(db_session, email="bye@example.com")
    user_id = user.id

    # Co-tenant + a device token; the device must be removed, the
    # other owner must survive.
    other = make_user(db_session, email="stayer@example.com")
    company = make_company(db_session, name="Shared Co")
    make_membership(db_session, user, company, role=CompanyRole.owner)
    make_membership(db_session, other, company, role=CompanyRole.owner)

    db_session.add(
        DeviceToken(
            user_id=user.id,
            token="dev-byebye",
            platform=DevicePlatform.android,
            app_version="1.0.0",
            is_active=True,
            last_active_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    req = _seed_grace_expired_request(db_session, user=user)

    processed = account_lifecycle_service.process_due_deletions(
        db_session, lambda: _worker_audit(db_session)
    )
    assert processed == 1
    db_session.expire_all()

    # The user row survives as an anonymised tombstone.
    tombstone = db_session.scalar(
        select(User).where(User.id == user_id)
    )
    assert tombstone is not None
    assert tombstone.email == f"deleted-{user_id}@deleted.invalid"
    assert tombstone.full_name == "Deleted user"
    assert tombstone.phone is None
    assert tombstone.firm_name is None
    assert tombstone.ca_membership_no is None
    assert tombstone.is_active is False
    assert tombstone.hashed_password == ""

    # Membership + device token explicitly deleted.
    assert db_session.scalar(
        select(UserCompany).where(UserCompany.user_id == user_id)
    ) is None
    assert db_session.scalar(
        select(DeviceToken).where(DeviceToken.token == "dev-byebye")
    ) is None

    # Request marked completed, NOT cascade-deleted.
    completed = db_session.scalar(
        select(AccountDeletionRequest).where(
            AccountDeletionRequest.id == req.id
        )
    )
    assert completed is not None
    assert completed.status == AccountDeletionStatus.completed
    assert completed.completed_at is not None

    # The other owner survives untouched.
    assert db_session.scalar(
        select(User).where(User.id == other.id)
    ) is not None


def test_process_due_emits_completion_audit_pointing_at_tombstone(
    db_session: Session,
) -> None:
    user = make_user(db_session, email="audited@example.com")
    user_id = user.id
    req = _seed_grace_expired_request(db_session, user=user)

    account_lifecycle_service.process_due_deletions(
        db_session, lambda: _worker_audit(db_session)
    )

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "account.deletion_completed",
            AuditLog.entity_id == req.id,
        )
    )
    assert audit is not None
    # FK still points at the tombstone (audit_logs are append-only;
    # we don't NULL the link).
    assert audit.user_id == user_id
    assert audit.source == "worker"


def test_process_due_skips_already_cancelled_request(
    db_session: Session,
) -> None:
    user = make_user(db_session, email="cancel-race@example.com")
    now = datetime.now(UTC)
    row = AccountDeletionRequest(
        user_id=user.id,
        requested_at=now - timedelta(days=31),
        grace_ends_at=now - timedelta(seconds=1),
        # Status flipped after find_due_requests collected the id —
        # service must double-check and skip.
        status=AccountDeletionStatus.cancelled,
        cancelled_at=now,
    )
    db_session.add(row)
    db_session.commit()

    # Direct entry, bypassing find_due:
    account_lifecycle_service.process_due_deletion(
        db_session, _worker_audit(db_session), request_id=row.id
    )
    db_session.commit()

    # User still present.
    assert db_session.scalar(
        select(User).where(User.id == user.id)
    ) is not None


def test_worker_task_runs_eagerly(
    db_session: Session,
) -> None:
    """The Celery task wrapper runs the same pipeline as the service."""
    user = make_user(db_session, email="celery@example.com")
    user_id = user.id
    _seed_grace_expired_request(db_session, user=user)

    # The task opens its own SessionLocal; running it inline via
    # `.apply()` commits inside that session, so expire local state
    # before re-querying.
    from app.workers import lifecycle_tasks

    result = lifecycle_tasks.process_due_account_deletions.apply()
    assert result.successful()
    assert result.result == 1

    db_session.expire_all()
    tombstone = db_session.scalar(select(User).where(User.id == user_id))
    assert tombstone is not None
    assert tombstone.email == f"deleted-{user_id}@deleted.invalid"
    assert tombstone.is_active is False
