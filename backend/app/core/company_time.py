"""Company-timezone-aware "today" -- the single place day boundaries for a
company are computed.

Fixes the UTC/IST day-boundary bug recorded in
`release/TAXMIND-PILOT-READINESS-2026-08-12.md` §18/§20: ``datetime.now(UTC)``
and the server-local ``date.today()`` both mislabel the day for ~5.5h every
night for an India user (00:00-05:29 IST is still "yesterday" in UTC).
Every endpoint that defaults a date to "today" for a company (dashboard,
reports) must go through ``company_today``, never call ``date.today()`` or
``datetime.now(UTC).date()`` directly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.company import Company


def company_today(company: Company, *, now: datetime | None = None) -> date:
    """Today's date in ``company``'s timezone.

    `now` is overridable (tests pin the clock); it may be naive (treated as
    UTC, matching every other UTC-default in this codebase) or aware in any
    timezone. Falls back to UTC if the stored timezone name is somehow
    invalid -- fail soft to a defined behavior, never raise on a read path.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    tz: tzinfo
    try:
        tz = ZoneInfo(company.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    return now.astimezone(tz).date()
