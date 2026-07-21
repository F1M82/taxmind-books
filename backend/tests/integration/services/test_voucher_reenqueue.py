"""BUG-Books-002: retryable-class voucher re-enqueue.

Covers the shared core in `app/services/tally/voucher_reenqueue.py`:

  * `select_retryable_strands` — picks ONLY strands whose latest
    tally-post audit action is `voucher.tally_post_queued`, honouring the
    attempt cap, the 30-day window, and the company filter; excludes
    rejection- and blocked-class strands (which need operator action).
  * `reenqueue_retryable_vouchers` — re-dispatches each strand via the
    real `dispatch_voucher_to_tally` with a fake registry, flipping a
    successful strand to `posted` and leaving an offline one pending.

No live Tally required — the registry is faked. The conftest default
`TAXMIND_SKIP_TALLY_DISPATCH=1` skips the dispatcher's BUG-005 ledger
guard, so `send_command` runs against the fake registry directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services.tally.connector_registry import ConnectorOffline
from app.services.tally.voucher_reenqueue import (
    MAX_REENQUEUE_ATTEMPTS,
    reenqueue_retryable_vouchers,
    select_retryable_strands,
)
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company, make_membership, make_user

_QUEUED = "voucher.tally_post_queued"
_FAILED = "voucher.tally_post_failed"
_BLOCKED = "voucher.tally_post_blocked"
_POSTED = "voucher.posted_to_tally"


# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


def _ledgers(db: Session, company) -> tuple[Ledger, Ledger]:  # type: ignore[no-untyped-def]
    party = Ledger(
        company_id=company.id,
        name="Xyz Ltd",
        name_normalized="xyz ltd",
        group_name="Sundry Debtors",
        tally_master_id="guid-xyz",
    )
    sales = Ledger(
        company_id=company.id,
        name="Sales",
        name_normalized="sales",
        group_name="Sales Accounts",
        tally_master_id="guid-sales",
    )
    db.add_all([party, sales])
    db.commit()
    return party, sales


def _strand(
    db: Session,
    company,  # type: ignore[no-untyped-def]
    ledgers: tuple[Ledger, Ledger],
    *,
    actions: list[str],
    attempts: int = 1,
    queued_ago: timedelta = timedelta(minutes=5),
    status: VoucherStatus = VoucherStatus.pending_tally_post,
) -> Voucher:
    """Create a voucher + 2 entries + a chain of tally-post audit rows.

    `actions` is emitted oldest→newest with strictly increasing
    `created_at`, so the LAST element is the strand's current class.
    """
    party, sales = ledgers
    now = datetime.now(UTC)
    v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Sales,
        date=now.date(),
        narration="strand",
        total_amount=Decimal("100.00"),
        status=status,
        source="manual",
        tally_post_queued_at=now - queued_ago,
        tally_post_attempts=attempts,
    )
    db.add(v)
    db.flush()
    db.add_all(
        [
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=party.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=v.id,
                ledger_id=sales.id,
                amount=Decimal("100.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    # Explicit, strictly-increasing created_at — Postgres now() is the
    # transaction timestamp, so rows in one txn would otherwise tie.
    for i, action in enumerate(actions):
        db.add(
            AuditLog(
                company_id=company.id,
                user_id=None,
                action=action,
                entity_type="voucher",
                entity_id=v.id,
                source="worker",
                created_at=now - timedelta(minutes=len(actions) - i),
            )
        )
    db.commit()
    return v


class _FakeRegistry:
    """Stand-in for ConnectorRegistry.send_command."""

    def __init__(self, *, outcome: str = "success") -> None:
        self.outcome = outcome
        self.idempotency_keys: list[str] = []

    async def send_command(
        self,
        *,
        company_id,  # type: ignore[no-untyped-def]
        command: str,
        args: dict,
        timeout_seconds: int,
        idempotency_key: str,
    ) -> dict:
        self.idempotency_keys.append(idempotency_key)
        if self.outcome == "offline":
            raise ConnectorOffline("no active connector for company")
        return {
            "status": "success",
            "result": {"tally_voucher_guid": "guid-ok"},
            "duration_ms": 3,
        }


def _company_with_owner(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.owner)
    return company


# ---------------------------------------------------------------------
# select_retryable_strands
# ---------------------------------------------------------------------


def test_selects_retryable_queued_strand(db_session: Session) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    v = _strand(db_session, company, ledgers, actions=[_QUEUED])

    picked = select_retryable_strands(db_session, company_id=company.id)
    assert [s.id for s in picked] == [v.id]


def test_excludes_rejection_and_blocked_strands(db_session: Session) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    _strand(db_session, company, ledgers, actions=[_QUEUED, _FAILED])  # rejected
    _strand(db_session, company, ledgers, actions=[_BLOCKED])  # unsynced
    keep = _strand(db_session, company, ledgers, actions=[_QUEUED])

    picked = select_retryable_strands(db_session, company_id=company.id)
    assert [s.id for s in picked] == [keep.id]


def test_latest_action_wins_failed_then_requeued(db_session: Session) -> None:
    """A strand rejected then re-queued (latest=queued) IS retryable again."""
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    v = _strand(db_session, company, ledgers, actions=[_FAILED, _QUEUED])

    picked = select_retryable_strands(db_session, company_id=company.id)
    assert [s.id for s in picked] == [v.id]


def test_excludes_posted_and_nonpending(db_session: Session) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    # Latest audit says posted.
    _strand(db_session, company, ledgers, actions=[_QUEUED, _POSTED])
    # Row already flipped to posted (belt-and-suspenders on status filter).
    _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        status=VoucherStatus.posted,
    )

    picked = select_retryable_strands(db_session, company_id=company.id)
    assert picked == []


def test_excludes_over_attempt_cap_and_stale(db_session: Session) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        attempts=MAX_REENQUEUE_ATTEMPTS,  # at cap → excluded
    )
    _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        queued_ago=timedelta(days=31),  # outside 30-day window → excluded
    )
    keep = _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        attempts=MAX_REENQUEUE_ATTEMPTS - 1,
    )

    picked = select_retryable_strands(db_session, company_id=company.id)
    assert [s.id for s in picked] == [keep.id]


def test_company_filter_scopes_selection(db_session: Session) -> None:
    company_a = _company_with_owner(db_session)
    company_b = _company_with_owner(db_session)
    la = _ledgers(db_session, company_a)
    lb = _ledgers(db_session, company_b)
    va = _strand(db_session, company_a, la, actions=[_QUEUED])
    vb = _strand(db_session, company_b, lb, actions=[_QUEUED])

    only_a = select_retryable_strands(db_session, company_id=company_a.id)
    assert [s.id for s in only_a] == [va.id]

    all_co = select_retryable_strands(db_session, company_id=None)
    assert {s.id for s in all_co} == {va.id, vb.id}


# ---------------------------------------------------------------------
# reenqueue_retryable_vouchers
# ---------------------------------------------------------------------


async def test_reenqueue_success_marks_posted(db_session: Session) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    v = _strand(db_session, company, ledgers, actions=[_QUEUED])
    reg = _FakeRegistry(outcome="success")

    posted = await reenqueue_retryable_vouchers(
        db_session, company_id=company.id, registry=reg
    )

    assert posted == 1
    # Idempotency key MUST be the voucher id — the connector-side cache
    # dedups against a post Tally already accepted (no double-post).
    assert reg.idempotency_keys == [str(v.id)]
    db_session.expire_all()
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.posted
    assert refreshed.tally_posted_at is not None


async def test_reenqueue_offline_leaves_pending_and_requeues(
    db_session: Session,
) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    v = _strand(db_session, company, ledgers, actions=[_QUEUED], attempts=1)
    reg = _FakeRegistry(outcome="offline")

    posted = await reenqueue_retryable_vouchers(
        db_session, company_id=company.id, registry=reg
    )

    assert posted == 0
    db_session.expire_all()
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.pending_tally_post
    # Dispatcher increments attempts + emits a fresh tally_post_queued
    # audit on ConnectorOffline, so the strand stays retryable.
    assert refreshed.tally_post_attempts == 2
    still = select_retryable_strands(db_session, company_id=company.id)
    assert [s.id for s in still] == [v.id]


async def test_reenqueue_noop_when_nothing_eligible(
    db_session: Session,
) -> None:
    company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    _strand(db_session, company, ledgers, actions=[_QUEUED, _FAILED])  # rejected
    reg = _FakeRegistry(outcome="success")

    posted = await reenqueue_retryable_vouchers(
        db_session, company_id=company.id, registry=reg
    )
    assert posted == 0
    assert reg.idempotency_keys == []
