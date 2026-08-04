"""Rebalance schedule inference and calendar expansion for quant models."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

ScheduleKind = Literal["weekly", "daily"]


def infer_schedule_from_strategy(code: str, params: dict) -> ScheduleKind:
    """Infer rebalance cadence from strategy params and source code heuristics.

    Priority:
    1. ``params["infer_schedule"]`` when set to ``weekly`` or ``daily``
    2. ``run_weekly`` in source → weekly
    3. ``run_daily`` in source → daily
    4. default weekly
    """
    params = params or {}
    explicit = params.get("infer_schedule")
    if explicit in ("weekly", "daily"):
        return explicit

    source = code or ""
    if "run_weekly" in source:
        return "weekly"
    if "run_daily" in source:
        return "daily"
    return "weekly"


def expand_rebalance_dates(
    schedule: ScheduleKind,
    start: date,
    end: date,
    *,
    weekday: int = 1,
) -> list[date]:
    """Expand rebalance calendar dates in ``[start, end]`` inclusive.

    ``weekday`` uses ISO-8601 numbering: Monday=1 .. Sunday=7 (matches Strategy V2
    ``run_weekly(..., weekday=1)``).
    """
    if start > end:
        return []

    if schedule == "daily":
        days = (end - start).days + 1
        return [start + timedelta(days=offset) for offset in range(days)]

    if schedule == "weekly":
        iso_weekday = max(1, min(7, int(weekday)))
        cursor = start
        # Advance to the first matching weekday on or after start.
        while cursor <= end and cursor.isoweekday() != iso_weekday:
            cursor += timedelta(days=1)
        dates: list[date] = []
        while cursor <= end:
            dates.append(cursor)
            cursor += timedelta(days=7)
        return dates

    raise ValueError(f"unsupported schedule: {schedule!r}")


def to_score_as_ofs(rebalance_dates: list[date], score_lag_days: int) -> list[date]:
    """Map rebalance dates to score ``as_of`` dates using calendar-day lag."""
    lag = int(score_lag_days)
    return [d - timedelta(days=lag) for d in rebalance_dates]
