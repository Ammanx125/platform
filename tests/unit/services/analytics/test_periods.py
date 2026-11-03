# tests/unit/services/analytics/test_periods.py
"""Reporting windows: the window a KPI's number covers."""

from datetime import date

import pytest

from app.services.analytics.periods import (
    PeriodError,
    period_bounds_utc,
    resolve_period,
)


def test_all_time_is_unbounded():
    period = resolve_period("all_time", anchor=date(2026, 6, 15))
    assert period.is_all_time
    assert period_bounds_utc(period) == (None, None)


def test_default_none_means_all_time():
    assert resolve_period(None, anchor=date(2026, 6, 15)).is_all_time


def test_month_window_covers_the_whole_month_of_the_anchor():
    period = resolve_period("month", anchor=date(2026, 2, 10))

    assert period.start == date(2026, 2, 1)
    # February 2026 has 28 days; end is exclusive.
    assert period.end == date(2026, 3, 1)


def test_last_three_months_spans_three_calendar_months():
    period = resolve_period("month", anchor=date(2026, 2, 10), count=3)

    assert period.start == date(2025, 12, 1)
    assert period.end == date(2026, 3, 1)


def test_quarter_window():
    period = resolve_period("quarter", anchor=date(2026, 5, 20))

    assert period.start == date(2026, 4, 1)
    assert period.end == date(2026, 7, 1)


def test_last_two_quarters_cross_the_year_boundary():
    period = resolve_period("quarter", anchor=date(2026, 2, 15), count=2)

    assert period.start == date(2025, 10, 1)
    assert period.end == date(2026, 4, 1)


def test_year_window():
    period = resolve_period("year", anchor=date(2026, 6, 1))

    assert period.start == date(2026, 1, 1)
    assert period.end == date(2027, 1, 1)


def test_day_and_week_windows():
    day = resolve_period("day", anchor=date(2026, 6, 15))
    assert (day.start, day.end) == (date(2026, 6, 15), date(2026, 6, 16))

    week = resolve_period("week", anchor=date(2026, 6, 15), count=2)
    assert (week.start, week.end) == (date(2026, 6, 8), date(2026, 6, 22))


def test_week_window_starts_on_monday_for_midweek_anchor():
    week = resolve_period("week", anchor=date(2026, 6, 17))

    assert (week.start, week.end) == (date(2026, 6, 15), date(2026, 6, 22))


def test_no_data_anchor_yields_an_empty_window_not_a_crash():
    """
    An empty dataset must read as "no rows in range", not raise: a KPI
    error is indistinguishable from a broken platform.
    """
    period = resolve_period("month", anchor=None)

    assert period.start == period.end
    assert period.label == "no data"


def test_zero_count_is_treated_as_one():
    assert resolve_period("month", anchor=date(2026, 6, 1), count=0).start == date(2026, 6, 1)


def test_unknown_period_is_rejected():
    with pytest.raises(PeriodError):
        resolve_period("fortnight", anchor=date(2026, 6, 1))


def test_bounds_are_timezone_aware():
    start, end = period_bounds_utc(resolve_period("month", anchor=date(2026, 2, 10)))
    assert start is not None
    assert end is not None
    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert start < end
