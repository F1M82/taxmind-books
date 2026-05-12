"""Unit tests for the FY-start helper (R4)."""

from __future__ import annotations

from datetime import date

from app.services.reporting.profit_loss import fiscal_year_start


def test_fy_start_in_post_april_returns_april_of_same_year() -> None:
    assert fiscal_year_start(date(2026, 5, 8)) == date(2026, 4, 1)
    assert fiscal_year_start(date(2026, 4, 1)) == date(2026, 4, 1)
    assert fiscal_year_start(date(2026, 12, 31)) == date(2026, 4, 1)


def test_fy_start_in_pre_april_returns_april_of_previous_year() -> None:
    assert fiscal_year_start(date(2026, 1, 15)) == date(2025, 4, 1)
    assert fiscal_year_start(date(2026, 3, 31)) == date(2025, 4, 1)
