"""Strategy V2 daily bars from ``qd_ashare_daily_bar``.

OHLC is forward-adjusted to the last factor inside the fetched window, so a
backtest never uses a dividend that happens after its own data. Lots, fees
and limit checks use the unadjusted prices carried alongside.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.utils.logger import get_logger

logger = get_logger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
PRICE_TICK = 0.005


def forward_adjust(raw: float | None, factor: float | None, end_factor: float | None) -> float | None:
    if raw is None:
        return None
    if factor is None or end_factor is None or end_factor <= 0 or factor <= 0:
        return raw
    return raw * factor / end_factor


def build_backtest_rows(
    bars: Iterable[Mapping[str, Any]],
    suspended_dates: Iterable[date] = (),
) -> list[dict[str, Any]]:
    """Turn stored unadjusted bars into kline dicts.

    ``limit_up`` / ``limit_down`` describe the open, because daily backtests
    fill at the open. A close that later touches the limit does not block it.
    """
    rows = []
    for bar in bars:
        day = _as_date(bar.get("trade_date"))
        if day is None:
            continue
        item = dict(bar)
        item["trade_date"] = day
        rows.append(item)
    rows.sort(key=lambda bar: bar["trade_date"])
    end_factor = next(
        (float(bar["adj_factor"]) for bar in reversed(rows) if bar.get("adj_factor")),
        None,
    )
    halted = {_as_date(day) for day in suspended_dates}
    halted.discard(None)
    out: list[dict[str, Any]] = []
    for bar in rows:
        day = bar["trade_date"]
        factor = float(bar["adj_factor"]) if bar.get("adj_factor") else None
        raw_open = _float(bar.get("open"))
        raw_high = _float(bar.get("high"))
        raw_low = _float(bar.get("low"))
        raw_close = _float(bar.get("close"))
        scale = 1.0
        if raw_close and factor and end_factor:
            scale = factor / end_factor
        up_limit = _float(bar.get("up_limit"))
        down_limit = _float(bar.get("down_limit"))
        opened_limit_up = (
            raw_open is not None and up_limit is not None and raw_open >= up_limit - PRICE_TICK
        )
        opened_limit_down = (
            raw_open is not None and down_limit is not None and raw_open <= down_limit + PRICE_TICK
        )
        out.append(
            {
                "time": int(datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).timestamp()),
                "open": forward_adjust(raw_open, factor, end_factor),
                "high": forward_adjust(raw_high, factor, end_factor),
                "low": forward_adjust(raw_low, factor, end_factor),
                "close": forward_adjust(raw_close, factor, end_factor),
                "volume": _float(bar.get("volume")) or 0.0,
                "amount": _float(bar.get("amount")),
                "turnover_rate": _float(bar.get("turnover_rate")),
                "raw_open": raw_open,
                "raw_close": raw_close,
                "up_limit": up_limit,
                "down_limit": down_limit,
                "price_scale": scale,
                "is_st": bool(bar.get("is_st")),
                "suspended": day in halted,
                "limit_up": opened_limit_up,
                "limit_down": opened_limit_down,
            }
        )
    return out


def fetch_platform_daily_klines(
    symbol: str,
    *,
    after_time: int | None = None,
    before_time: int | None = None,
) -> list[dict[str, Any]]:
    """Read one symbol from the data platform. Empty when it has no rows."""
    from app.markets.cn_stock.symbols import canonicalize_cn_symbol
    from app.utils.db import get_db_connection

    code = canonicalize_cn_symbol(str(symbol or ""))
    if not code:
        return []
    start = _unix_date(after_time) or date(1990, 1, 1)
    end = _unix_date(before_time) or date(2100, 1, 1)
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT trade_date, open, high, low, close, volume, amount,
                       turnover_rate, adj_factor, up_limit, down_limit, is_st
                FROM qd_ashare_daily_bar
                WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (code, start, end),
            )
            bars = cur.fetchall() or []
            cur.execute(
                """
                SELECT trade_date FROM qd_ashare_suspension
                WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
                  AND suspend_type = 'S' AND suspend_timing = ''
                """,
                (code, start, end),
            )
            halted = [row["trade_date"] for row in (cur.fetchall() or [])]
            cur.close()
    except Exception as exc:
        logger.warning("A-share platform daily miss for %s: %s", code, exc)
        return []
    return build_backtest_rows(bars, halted)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _unix_date(value: int | None) -> date | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), SHANGHAI).date()
