"""company_today -- the fix for the UTC/IST dashboard day-boundary bug.

release/TAXMIND-PILOT-READINESS-2026-08-12.md §18/§20: for ~5.5h every
night (00:00-05:29 IST), UTC-anchored "today" mislabels the prior IST
day's data as "today" for every India user. These are pure-function tests
(no DB) -- Company is constructed in-memory, never persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.core.company_time import company_today
from app.models.company import Company


def _company(tz: str = "Asia/Kolkata") -> Company:
    return Company(name="Test Co", timezone=tz)


def test_late_utc_evening_is_already_tomorrow_in_ist() -> None:
    # The core bug: 2026-09-15T19:00:00Z is 2026-09-16T00:30:00+05:30 --
    # already the *next* day in India, even though the UTC calendar date
    # hasn't rolled over yet.
    now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert company_today(_company(), now=now).isoformat() == "2026-09-16"


def test_deep_in_the_ist_bug_window_is_still_the_next_day() -> None:
    # The worst point in the documented 00:00-05:29 IST window: naive UTC
    # .date() on this instant reads 2026-09-15 (the bug), but it's already
    # 2026-09-16 04:30 in India.
    now = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)  # 2026-09-16 04:30 IST
    assert company_today(_company(), now=now).isoformat() == "2026-09-16"


def test_matches_utc_date_mid_ist_day() -> None:
    # Comfortably inside the IST day with no boundary crossing: UTC and
    # IST calendar dates agree.
    now = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)  # 2026-09-15 15:30 IST
    assert company_today(_company(), now=now).isoformat() == "2026-09-15"


def test_naive_now_is_treated_as_utc() -> None:
    naive = datetime(2026, 9, 15, 19, 0)  # no tzinfo
    aware = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert company_today(_company(), now=naive) == company_today(
        _company(), now=aware
    )


def test_non_ist_now_is_normalized_correctly() -> None:
    # An already-aware `now` in a different offset must still convert
    # through the company's timezone correctly, not be used as-is.
    # 2026-09-15T21:00:00-05:00 == 2026-09-16T02:00:00Z == 2026-09-16 07:30 IST
    now = datetime(2026, 9, 15, 21, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert company_today(_company(), now=now).isoformat() == "2026-09-16"


def test_utc_company_uses_the_utc_calendar_date() -> None:
    now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert company_today(_company("UTC"), now=now).isoformat() == "2026-09-15"


def test_invalid_timezone_name_falls_back_to_utc_without_raising() -> None:
    now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    assert (
        company_today(_company("Not/A/Real/Zone"), now=now).isoformat()
        == "2026-09-15"
    )
