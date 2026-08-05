"""A-share daily bar sync gate (close / calendar).

Independent copy of rdagent-bridge semantics — keep in sync when changing rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")
CLOSE_HOUR = 15
CLOSE_MINUTE = 5


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

    if is_open_fn is None:
        # default: Mon–Fri only (tests inject Tushare-backed fn in production path)
        trading = target.weekday() < 5
    else:
        trading = bool(is_open_fn(as_of_s))

    if target > today:
        return AshareSyncDecision(False, "future", as_of_s, trading)
    if not trading:
        return AshareSyncDecision(False, "not_trading_day", as_of_s, False)
    if target == today:
        closed = (now_sh.hour, now_sh.minute) >= (CLOSE_HOUR, CLOSE_MINUTE)
        if not closed:
            return AshareSyncDecision(False, "not_closed", as_of_s, True)
    return AshareSyncDecision(True, "ok", as_of_s, True)
