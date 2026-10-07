"""v1.3 P0.54: the 30-day expiry sweep for retryable-class strands.

Covers the expiry half of `app/services/tally/voucher_reenqueue.py`:

  * `select_expired_strands` — the window complement of
    `select_retryable_strands`: queued-class strands older than
    REENQUEUE_WINDOW only; rejection/blocked strands stay put.
  * `expire_stranded_vouchers` — flips them to
    `VoucherStatus.tally_post_expired`, emits one
    `voucher.tally_post_expired` audit row, pushes a notification to
    the voucher's creator via the P0.44 `send_to_user` channel, and is
    idempotent on re-run.
  * Reports/onboarding exclusion — `tally_post_expired` is NOT a live
    book entry, so the P0.46d allow-list filters
    (`status.in_([posted, pending_tally_post])`) must keep it out of
    trial balance, P&L, and the onboarding checklist.

No live Tally required — the expiry sweep is DB-only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.audit_log import AuditLog
from app.models.company import CompanyRole
from app.models.ledger import BalanceType, Ledger
from app.models.voucher import (
    EntryType,
    LedgerEntry,
    Voucher,
    VoucherStatus,
    VoucherType,
)
from app.services import notification_service
from app.services.onboarding_service import build_checklist
from app.services.reporting.profit_loss import compute_profit_loss
from app.services.reporting.trial_balance import compute_trial_balance
from app.services.tally.voucher_reenqueue import (
    REENQUEUE_WINDOW,
    expire_stranded_vouchers,
    select_expired_strands,
)
from sqlalchemy.orm import Session

from tests._db_fixtures import make_company, make_membership, make_user

_QUEUED = "voucher.tally_post_queued"
_FAILED = "voucher.tally_post_failed"
_BLOCKED = "voucher.tally_post_blocked"
_POSTED = "voucher.posted_to_tally"

_OVERDUE = REENQUEUE_WINDOW + timedelta(days=1)


# ---------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------


def _strand(
    db: Session,
    company,  # type: ignore[no-untyped-def]
    ledgers: tuple[Ledger, Ledger],
    *,
    actions: list[str],
    created_by=None,  # type: ignore[no-untyped-def]
    queued_ago: timedelta = timedelta(minutes=5),
    status: VoucherStatus = VoucherStatus.pending_tally_post,
    voucher_date: date | None = None,
) -> Voucher:
    """A voucher + 2 entries + a tally-post audit chain, like the
    re-enqueue suite's helper. The LAST action is the current class."""
    party, sales = ledgers
    now = datetime.now(UTC)
    v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Sales,
        date=voucher_date or now.date(),
        narration="strand",
        total_amount=Decimal("100.00"),
        status=status,
        source="manual",
        tally_post_queued_at=now - queued_ago,
        tally_post_attempts=1,
        created_by=created_by.id if created_by is not None else None,
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


def _company_with_owner(db: Session):  # type: ignore[no-untyped-def]
    user = make_user(db)
    company = make_company(db)
    make_membership(db, user, company, role=CompanyRole.owner)
    return user, company


def _ledgers(db: Session, company) -> tuple[Ledger, Ledger]:  # type: ignore[no-untyped-def]
    party = Ledger(
        company_id=company.id,
        name="Xyz Ltd",
        name_normalized="xyz ltd",
        group_name="Sundry Debtors",
        balance_type=BalanceType.Dr,
    )
    sales = Ledger(
        company_id=company.id,
        name="Sales",
        name_normalized="sales",
        group_name="Sales Accounts",
        balance_type=BalanceType.Cr,
    )
    db.add_all([party, sales])
    db.commit()
    return party, sales


# ---------------------------------------------------------------------
# expire_stranded_vouchers
# ---------------------------------------------------------------------


def test_overdue_queued_strand_is_expired_with_audit_and_notification(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    v = _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        created_by=user,
        queued_ago=_OVERDUE,
    )

    sent: list = []
    monkeypatch.setattr(
        notification_service,
        "send_to_user",
        lambda db, *, user_id, notification: sent.append(
            (user_id, notification)
        )
        or [],
    )

    expired = expire_stranded_vouchers(db_session, company_id=company.id)
    db_session.expire_all()

    assert expired == 1
    refreshed = db_session.get(Voucher, v.id)
    assert refreshed.status == VoucherStatus.tally_post_expired

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "voucher",
            AuditLog.entity_id == v.id,
            AuditLog.action == "voucher.tally_post_expired",
        )
        .one()
    )
    assert audit.old_value["status"] == "pending_tally_post"
    assert audit.new_value["status"] == "tally_post_expired"
    assert audit.new_value["window_days"] == REENQUEUE_WINDOW.days
    assert audit.company_id == company.id

    # Exactly one push, to the voucher's creator, identifying the voucher.
    assert len(sent) == 1
    notified_user_id, notification = sent[0]
    assert notified_user_id == user.id
    assert notification.data["voucher_id"] == str(v.id)
    assert notification.data["company_id"] == str(company.id)
    assert notification.data["kind"] == "voucher.tally_post_expired"

    # Idempotent: an expired voucher no longer matches the selection.
    assert expire_stranded_vouchers(db_session, company_id=company.id) == 0
    assert len(sent) == 1  # no double notification


def test_recent_or_nonretryable_strands_are_left_alone(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    recent = _strand(
        db_session, company, ledgers, actions=[_QUEUED]
    )  # 5 minutes old
    rejected = _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED, _FAILED],
        queued_ago=_OVERDUE,
    )
    blocked = _strand(
        db_session, company, ledgers, actions=[_BLOCKED], queued_ago=_OVERDUE
    )
    posted = _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED, _POSTED],
        queued_ago=_OVERDUE,
        status=VoucherStatus.posted,
    )

    sent: list = []
    monkeypatch.setattr(
        notification_service,
        "send_to_user",
        lambda db, *, user_id, notification: sent.append(user_id) or [],
    )

    assert select_expired_strands(db_session, company_id=company.id) == []
    assert expire_stranded_vouchers(db_session, company_id=company.id) == 0
    for v in (recent, rejected, blocked, posted):
        db_session.expire_all()
        assert db_session.get(Voucher, v.id).status != (
            VoucherStatus.tally_post_expired
        )
    assert sent == []


def test_company_filter_scopes_expiry(
    db_session: Session,
) -> None:
    _, company_a = _company_with_owner(db_session)
    user_b = make_user(db_session)
    company_b = make_company(db_session)
    make_membership(db_session, user_b, company_b, role=CompanyRole.owner)
    ledgers_a = _ledgers(db_session, company_a)
    ledgers_b = _ledgers(db_session, company_b)
    _strand(
        db_session,
        company_a,
        ledgers_a,
        actions=[_QUEUED],
        queued_ago=_OVERDUE,
    )
    _strand(
        db_session,
        company_b,
        ledgers_b,
        actions=[_QUEUED],
        queued_ago=_OVERDUE,
    )

    # The connector-up trigger scopes to one company; the periodic sweep
    # passes None. Both selections must respect the filter.
    picked_a = select_expired_strands(db_session, company_id=company_a.id)
    assert [s.company_id for s in picked_a] == [company_a.id]
    picked_all = select_expired_strands(db_session, company_id=None)
    assert {s.company_id for s in picked_all} == {company_a.id, company_b.id}


# ---------------------------------------------------------------------
# Reports/onboarding exclusion (P0.46d allow-lists vs tally_post_expired)
# ---------------------------------------------------------------------


def test_expired_voucher_excluded_from_reports_and_onboarding(
    db_session: Session,
) -> None:
    """`tally_post_expired` is not a live book entry: trial balance, P&L,
    and the onboarding checklist must treat it like `rejected_optional`,
    not like `pending_tally_post`."""
    user, company = _company_with_owner(db_session)
    ledgers = _ledgers(db_session, company)
    party, sales = ledgers

    # A posted ₹1000 credit sale — the real book entry.
    posted_v = Voucher(
        company_id=company.id,
        voucher_type=VoucherType.Sales,
        date=date(2026, 5, 1),
        total_amount=Decimal("1000.00"),
        status=VoucherStatus.posted,
        source="manual",
    )
    db_session.add(posted_v)
    db_session.flush()
    db_session.add_all(
        [
            LedgerEntry(
                company_id=company.id,
                voucher_id=posted_v.id,
                ledger_id=party.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Dr,
                line_number=1,
            ),
            LedgerEntry(
                company_id=company.id,
                voucher_id=posted_v.id,
                ledger_id=sales.id,
                amount=Decimal("1000.00"),
                entry_type=EntryType.Cr,
                line_number=2,
            ),
        ]
    )
    db_session.commit()

    def _tb_total() -> Decimal:
        tb = compute_trial_balance(
            db_session, company_id=company.id, as_of_date=date(2026, 5, 31)
        )
        return sum(
            (abs(r.closing_balance) for r in tb.rows), Decimal("0")
        )

    def _pnl_net() -> Decimal:
        pnl = compute_profit_loss(
            db_session,
            company_id=company.id,
            from_date=date(2026, 5, 1),
            to_date=date(2026, 5, 31),
        )
        return pnl.net_value

    def _first_voucher_done() -> bool:
        checklist = build_checklist(db_session, company=company)
        item = next(
            i for i in checklist.items if i.key == "first_voucher_posted"
        )
        return item.completed

    assert _tb_total() == Decimal("2000.00")  # Dr 1000 + Cr 1000
    with_posted_tb = _tb_total()
    with_posted_pnl = _pnl_net()

    # Now strand the same ledgers ₹100 overdue and expire it. A Sales
    # voucher: Dr party / Cr sales — while queued it lifts income by 100
    # and both TB sides by 100.
    overdue = _strand(
        db_session,
        company,
        ledgers,
        actions=[_QUEUED],
        created_by=user,
        queued_ago=_OVERDUE,
        voucher_date=date(2026, 5, 2),
    )
    assert _tb_total() == with_posted_tb + Decimal("200.00")  # queued counts
    assert _pnl_net() == with_posted_pnl + Decimal("100.00")
    assert _first_voucher_done() is True

    assert (
        expire_stranded_vouchers(db_session, company_id=company.id) == 1
    )
    db_session.expire_all()
    assert db_session.get(Voucher, overdue.id).status == (
        VoucherStatus.tally_post_expired
    )

    # Back to exactly the posted voucher's numbers — expired excluded.
    assert _tb_total() == with_posted_tb
    assert _pnl_net() == with_posted_pnl
    # And the checklist milestone survives (it already was True — the
    # point is the expired row neither creates nor destroys it).
    assert _first_voucher_done() is True
