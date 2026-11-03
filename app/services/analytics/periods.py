# app/services/analytics/periods.py
"""
Reporting windows for KPI evaluation.

A KPI's number means nothing without the window it covers: "gross profit" is
an all-time figure, a monthly figure, and a quarterly figure depending on
which rows are summed. This module turns a KPI definition's period into a
concrete date range.

The window is anchored to the tenant's newest data point, not to now().
A dataset uploaded today may contain last quarter's transactions, and a
"last month" computed from wall-clock time would report an empty window —
which reads as "revenue dropped to zero" rather than "no rows in range".
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

PERIODS = (
    "all_time",
    "day",
    "week",
    "month",
    "quarter",
    "year",
)


class PeriodError(ValueError):
    """The requested period cannot be resolved."""


@dataclass(frozen=True)
class Period:
    """A resolved window. `start` is inclusive, `end` exclusive."""

    kind: str
    start: date | None
    end: date | None
    label: str

    @property
    def is_all_time(self) -> bool:
        return self.start is None and self.end is None


def _subtract_periods(anchor: date, kind: str, count: int) -> date:
    """Start of the window `count` periods before the period containing anchor."""
    if kind == "day":
        return anchor - timedelta(days=count)
    if kind == "week":
        week_start = anchor - timedelta(days=anchor.weekday())
        return week_start - timedelta(weeks=count)
    if kind == "month":
        month_index = (anchor.year * 12 + anchor.month - 1) - count
        year, month = divmod(month_index, 12)
        return date(year, month + 1, 1)
    if kind == "quarter":
        quarter_index = (anchor.year * 4) + ((anchor.month - 1) // 3) - count
        year, quarter = divmod(quarter_index, 4)
        return date(year, quarter * 3 + 1, 1)
    if kind == "year":
        return date(anchor.year - count, 1, 1)
    raise PeriodError(f"unknown period {kind!r}")


def _period_end(anchor: date, kind: str) -> date:
    """Exclusive end of the period containing `anchor`."""
    if kind == "day":
        return anchor + timedelta(days=1)
    if kind == "week":
        week_start = anchor - timedelta(days=anchor.weekday())
        return week_start + timedelta(weeks=1)
    if kind == "month":
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        return date(anchor.year, anchor.month, last_day) + timedelta(days=1)
    if kind == "quarter":
        quarter_end_month = ((anchor.month - 1) // 3) * 3 + 3
        last_day = calendar.monthrange(anchor.year, quarter_end_month)[1]
        return date(anchor.year, quarter_end_month, last_day) + timedelta(days=1)
    if kind == "year":
        return date(anchor.year + 1, 1, 1)
    raise PeriodError(f"unknown period {kind!r}")


def resolve_period(
    kind: str | None,
    *,
    anchor: date | None,
    count: int = 1,
) -> Period:
    """
    Resolve a KPI's period into a concrete window.

    anchor is the newest data point in scope. When it is None there is no
    data, so a bounded period resolves to an empty window at the epoch
    rather than raising: the KPI should report "no rows in range", not fail.

    A non-positive `count` is treated as 1.
    """
    if not kind or kind == "all_time":
        return Period(kind="all_time", start=None, end=None, label="all time")

    if kind not in PERIODS:
        raise PeriodError(f"unknown period {kind!r}; expected one of {sorted(PERIODS)}")

    periods = max(1, int(count or 1))

    if anchor is None:
        empty_day = date(1970, 1, 1)
        return Period(kind=kind, start=empty_day, end=empty_day, label="no data")

    start = _subtract_periods(anchor, kind, periods - 1)
    end = _period_end(anchor, kind)
    label = f"last {periods} {kind}{'' if periods == 1 else 's'}"
    return Period(kind=kind, start=start, end=end, label=label)


def period_bounds_utc(period: Period) -> tuple[datetime | None, datetime | None]:
    """The window as timezone-aware datetimes, for row-timestamp queries."""
    if period.is_all_time:
        return None, None
    start = period.start or date(1970, 1, 1)
    end = period.end or start
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC),
    )
