"""A-share daily bar sync gate (close / calendar).

Independent copy of rdagent-bridge semantics — keep in sync when changing rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")
CLOSE_HOUR = 15
CLOSE_MINUTE = 5

_XSHG_CALENDAR = None


def _xshg_calendar():
    global _XSHG_CALENDAR
    if _XSHG_CALENDAR is not None:
        return _XSHG_CALENDAR
    try:
        import exchange_calendars as exchange_calendars

        _XSHG_CALENDAR = exchange_calendars.get_calendar("XSHG")
    except Exception:
        _XSHG_CALENDAR = False
    return _XSHG_CALENDAR


def is_ashare_trading_day(
    as_of: date | str,
    *,
    is_open_fn: Callable[[str], bool] | None = None,
) -> bool:
    """True when *as_of* is an A-share (XSHG) trading session day."""
    target = _parse_as_of(as_of)
    if target is None:
        return False
    as_of_s = target.isoformat()
    if is_open_fn is not None:
        return bool(is_open_fn(as_of_s))
    cal = _xshg_calendar()
    if cal:
        import pandas as pd

        return bool(cal.is_session(pd.Timestamp(as_of_s)))
    # Fallback when exchange_calendars is unavailable (unit tests).
    return target.weekday() < 5


def latest_ashare_trading_day(on_or_before: date | str) -> date | None:
    """Newest A-share session day on/before *on_or_before* (looks back 30 days)."""
    target = _parse_as_of(on_or_before)
    if target is None:
        return None
    for offset in range(31):
        day = target - timedelta(days=offset)
        if is_ashare_trading_day(day):
            return day
    return None


@dataclass(frozen=True)
class AshareSyncDecision:
    ok: bool
    reason: str
    as_of: str
    trading_day: bool


def _parse_as_of(value: date | str) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def decide_ashare_daily_sync(
    as_of: date | str,
    *,
    now: datetime | None = None,
    is_open_fn: Callable[[str], bool] | None = None,
) -> AshareSyncDecision:
    target = _parse_as_of(as_of)
    if target is None:
        return AshareSyncDecision(False, "invalid_date", "", False)
    as_of_s = target.isoformat()
    now_sh = now.astimezone(SH_TZ) if now else datetime.now(SH_TZ)
    today = now_sh.date()

    trading = is_ashare_trading_day(target, is_open_fn=is_open_fn)

    if target > today:
        return AshareSyncDecision(False, "future", as_of_s, trading)
    if not trading:
        return AshareSyncDecision(False, "not_trading_day", as_of_s, False)
    if target == today:
        closed = (now_sh.hour, now_sh.minute) >= (CLOSE_HOUR, CLOSE_MINUTE)
        if not closed:
            return AshareSyncDecision(False, "not_closed", as_of_s, True)
    return AshareSyncDecision(True, "ok", as_of_s, True)
