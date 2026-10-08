from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _rest_api_key() -> str:
    return str(os.environ.get("TUSHARE_API_KEY") or "").strip()


def is_tushare_configured() -> bool:
    return bool(_rest_api_key() or str(os.environ.get("TUSHARE_TOKEN") or "").strip())


def tencent_code_to_ts_code(code: str) -> str:
    s = (code or "").strip().upper()
    if not s:
        return s
    if s.endswith(".SH") or s.endswith(".SZ"):
        return s
    if s.startswith("SH") and len(s) >= 8 and s[2:].isdigit():
        return f"{s[2:]}.SH"
    if s.startswith("SZ") and len(s) >= 8 and s[2:].isdigit():
        return f"{s[2:]}.SZ"
    if s.isdigit() and len(s) == 6:
        return f"{s}.SH" if s.startswith("6") else f"{s}.SZ"
    return s


def is_ashare_index_ts_code(ts_code: str) -> bool:
    """True for SSE/SZSE index codes that need Tushare ``index_daily``.

    Examples: ``000300.SH`` (CSI300), ``000905.SH`` (CSI500), ``399006.SZ``.
    Note: ``000001.SZ`` is a stock and must keep using ``daily``.
    """
    s = str(ts_code or "").strip().upper()
    if "." not in s:
        return False
    code, exch = s.split(".", 1)
    if not (code.isdigit() and len(code) == 6):
        return False
    if exch == "SH" and code.startswith("000"):
        return True
    if exch == "SZ" and code.startswith("399"):
        return True
    return False


_pro_cache: Dict[tuple[str, ...], Any] = {}


def _build_pro():
    api_key = _rest_api_key()
    if api_key:
        from app.data_sources.tushare_rest import (
            DEFAULT_BACKUP_URL,
            DEFAULT_REST_URL,
            TushareRestPro,
        )

        base_url = str(os.environ.get("TUSHARE_REST_URL") or "").strip() or DEFAULT_REST_URL
        backup_url = (
            str(os.environ.get("TUSHARE_REST_BACKUP_URL") or "").strip() or DEFAULT_BACKUP_URL
        )
        cache_key = ("rest", api_key, base_url, backup_url)
        cached = _pro_cache.get(cache_key)
        if cached is None:
            cached = TushareRestPro(api_key, base_url=base_url, backup_url=backup_url)
            _pro_cache[cache_key] = cached
        return cached

    import tushare as ts

    token = str(os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        return None
    http_url = str(os.environ.get("TUSHARE_HTTP_URL") or "").strip()
    cache_key = ("sdk", token, http_url)
    cached = _pro_cache.get(cache_key)
    if cached is not None:
        return cached
    pro = ts.pro_api(token)
    if http_url:
        # 运营商自定义基址（与官方 pro 兼容的 DataApi）
        pro._DataApi__http_url = http_url
    _pro_cache[cache_key] = pro
    return pro


def _trade_date_to_unix(trade_date: str) -> int:
    # A-share trade_date is a Shanghai calendar day; use local midnight so charts
    # show 2026-07-29 00:00+08 instead of UTC midnight rendered as 08:00+08.
    dt = datetime.strptime(str(trade_date), "%Y%m%d").replace(tzinfo=_SHANGHAI)
    return int(dt.timestamp())


def daily_bars_cover_today(rows: List[Dict[str, Any]]) -> bool:
    """True when the newest bar reaches the expected Shanghai session day.

    On a trading day that is today; on weekends / holidays it is the latest
    session before today, so Tushare stays the primary source when no new bar
    can exist.
    """
    if not rows:
        return False
    try:
        last = max(int(r.get("time") or 0) for r in rows)
    except (TypeError, ValueError):
        return False
    if last <= 0:
        return False
    from app.services.market.ashare_session import latest_ashare_trading_day

    last_day = datetime.fromtimestamp(last, tz=_SHANGHAI).date()
    today = datetime.now(_SHANGHAI).date()
    expected = latest_ashare_trading_day(today) or today
    return last_day >= expected


def fetch_tushare_daily_klines(
    *,
    tencent_code: str,
    limit: int,
    before_time: Optional[int] = None,
    after_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not is_tushare_configured():
        return []
    pro = _build_pro()
    if pro is None:
        return []
    ts_code = tencent_code_to_ts_code(tencent_code)
    lim = max(int(limit or 1), 1)
    end_dt = (
        datetime.fromtimestamp(int(before_time), tz=timezone.utc)
        if before_time
        else datetime.now(_SHANGHAI)
    )
    # Pair start_date with end_date. The backup gateway rejects a one-sided range.
    end_date = end_dt.strftime("%Y%m%d")
    # Bound the window (≈1.6 calendar days per session + holiday slack) instead
    # of pulling a symbol's entire history on every call.
    start_dt = end_dt - timedelta(days=int(lim * 1.6) + 30)
    if after_time:
        start_dt = max(
            start_dt,
            datetime.fromtimestamp(int(after_time), tz=timezone.utc) - timedelta(days=1),
        )
    start_date = start_dt.strftime("%Y%m%d")
    use_index = is_ashare_index_ts_code(ts_code)
    api_name = "index_daily" if use_index else "daily"
    factors: Optional[Dict[str, float]] = None
    df = None
    if not use_index:
        snapshot = _market_window(
            pro,
            ts_code,
            start_dt.astimezone(_SHANGHAI).date(),
            end_dt.astimezone(_SHANGHAI).date(),
        )
        if snapshot is not None and snapshot[0] is not None:
            df, factors = snapshot
    if df is None:
        try:
            df = _fetch_range(pro, api_name, ts_code=ts_code, start_date=start_date, end_date=end_date)
        except Exception as exc:
            logger.warning("Tushare %s failed ts_code=%s: %s", api_name, ts_code, exc)
            return []
    # Index codes occasionally mis-routed: retry the other endpoint once.
    if (df is None or getattr(df, "empty", True)) and not use_index and ts_code.endswith(".SH"):
        try:
            df = _fetch_range(
                pro, "index_daily", ts_code=ts_code, start_date=start_date, end_date=end_date
            )
            api_name = "index_daily"
        except Exception as exc:
            logger.warning("Tushare index_daily fallback failed ts_code=%s: %s", ts_code, exc)
            return []
    if df is None or getattr(df, "empty", True):
        return []
    df = df.sort_values("trade_date")
    if before_time:
        df = df[df["trade_date"].map(_trade_date_to_unix) < int(before_time)]
    df = df.tail(lim)
    meta = (
        _ashare_daily_meta(pro, ts_code, df, start_date, end_date, factors=factors)
        if api_name == "daily"
        else {}
    )
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            key = _trade_date_key(row["trade_date"])
            info = meta.get(key) or {}
            ratio = float(info.get("ratio") or 1.0)
            raw_volume = row["vol"] if "vol" in row.index else row.get("volume")
            volume = float(raw_volume or 0.0)
            suspended = bool(info.get("suspended")) or (
                raw_volume is not None and not pd.isna(raw_volume) and volume == 0.0
            )
            limit_up = bool(info.get("limit_up"))
            limit_down = bool(info.get("limit_down"))
            bar = {
                "time": _trade_date_to_unix(row["trade_date"]),
                "open": float(row["open"]) * ratio,
                "high": float(row["high"]) * ratio,
                "low": float(row["low"]) * ratio,
                "close": float(row["close"]) * ratio,
                # Tushare vol 单位为手；保持数值即可，与现网一致即可用
                "volume": volume,
            }
            if api_name == "daily":
                bar.update(
                    {
                        "limit_up": limit_up,
                        "is_limit_up": limit_up,
                        "limit_down": limit_down,
                        "is_limit_down": limit_down,
                        "suspended": suspended,
                        "is_suspended": suspended,
                    }
                )
            out.append(bar)
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _trade_date_key(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    if text.endswith(".0"):
        text = text[:-2]
    return text[:8]


# The relay rejects closed ranges longer than about a year (date_range_too_large).
_RANGE_CHUNK_DAYS = 180


def _fetch_range(
    pro: Any,
    api_name: str,
    *,
    start_date: str,
    end_date: Optional[str],
    **params: Any,
) -> Optional[pd.DataFrame]:
    """Call a date-range API in chunks of at most ``_RANGE_CHUNK_DAYS`` days."""
    api = getattr(pro, api_name)
    if not end_date:
        return api(start_date=start_date, end_date=end_date, **params)
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=_RANGE_CHUNK_DAYS - 1))
        frame = api(
            start_date=cursor.strftime("%Y%m%d"),
            end_date=chunk_end.strftime("%Y%m%d"),
            **params,
        )
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            frames.append(frame)
        cursor = chunk_end + timedelta(days=1)
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if "trade_date" in merged.columns:
        merged = merged.drop_duplicates(subset=[c for c in ("ts_code", "trade_date") if c in merged.columns])
    return merged


def _optional_frame(pro: Any, api_name: str, **params: Any) -> Optional[pd.DataFrame]:
    """Best-effort lookup. A missing gateway must not drop the price bars."""
    try:
        frame = _fetch_range(pro, api_name, **params)
    except Exception as exc:
        logger.info("Tushare %s skipped ts_code=%s: %s", api_name, params.get("ts_code"), exc)
        return None
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    return frame


def _board_limit_pct(ts_code: str, *, st: bool) -> float:
    if st:
        return 0.05
    code = str(ts_code or "").split(".", 1)[0]
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _at_up_limit(price: float, limit: float) -> bool:
    if price <= 0 or limit <= 0:
        return False
    return price + 1e-6 >= limit - max(0.005, limit * 1e-4)


def _at_down_limit(price: float, limit: float) -> bool:
    if price <= 0 or limit <= 0:
        return False
    return price - 1e-6 <= limit + max(0.005, limit * 1e-4)


# Exact limit/suspend/ST lookups cover the recent sessions only; older bars use
# the board band and zero-volume fallback. Each date is one market-wide call
# shared by every symbol instead of three calls per symbol.
_FLAG_RECENT_SESSIONS = 30
_FLAG_RETRY_SECONDS = 600.0
_market_flags: Dict[str, Dict[str, Any]] = {}
_market_flag_failures: Dict[str, float] = {}
_market_flags_lock = threading.Lock()


def _market_day_flags(pro: Any, trade_date: str) -> Optional[Dict[str, Any]]:
    if len(trade_date) != 8:
        return None
    with _market_flags_lock:
        cached = _market_flags.get(trade_date)
        if cached is not None:
            return cached
        failed_at = _market_flag_failures.get(trade_date)
        if failed_at is not None and time.monotonic() - failed_at < _FLAG_RETRY_SECONDS:
            return None
        try:
            limit_frame = getattr(pro, "stk_limit")(trade_date=trade_date)
            suspend_frame = getattr(pro, "suspend_d")(trade_date=trade_date)
            st_frame = getattr(pro, "stock_st")(trade_date=trade_date)
        except Exception as exc:
            logger.info("Tushare market flags skipped trade_date=%s: %s", trade_date, exc)
            _market_flag_failures[trade_date] = time.monotonic()
            return None
        limits: Dict[str, tuple[float, float]] = {}
        if isinstance(limit_frame, pd.DataFrame) and {"ts_code", "up_limit", "down_limit"} <= set(limit_frame.columns):
            for code, up, down in limit_frame[["ts_code", "up_limit", "down_limit"]].itertuples(index=False):
                try:
                    limits[str(code)] = (float(up), float(down))
                except (TypeError, ValueError):
                    continue
        suspended: set[str] = set()
        if isinstance(suspend_frame, pd.DataFrame) and "ts_code" in suspend_frame.columns:
            for _, row in suspend_frame.iterrows():
                kind = str(row.get("suspend_type") or "").strip().upper()
                if kind not in {"R", "RESUME"}:
                    suspended.add(str(row["ts_code"]))
        st: set[str] = set()
        if isinstance(st_frame, pd.DataFrame) and "ts_code" in st_frame.columns:
            st = {str(code) for code in st_frame["ts_code"].tolist()}
        snapshot = {"limits": limits, "suspended": suspended, "st": st}
        _market_flags[trade_date] = snapshot
        _market_flag_failures.pop(trade_date, None)
        return snapshot


# Short windows (live strategy loads) are served from market-wide ``daily`` and
# ``adj_factor`` snapshots: two requests per session for the whole pool instead
# of several per symbol, which the relay throttles with upstream_pool_exhausted.
_MARKET_WINDOW_MAX_DAYS = 45
_MARKET_DAY_RETRY_SECONDS = 120.0
_MARKET_DAY_CACHE_SIZE = 240
_market_days: Dict[tuple[str, str], pd.DataFrame] = {}
_market_day_failures: Dict[tuple[str, str], float] = {}
_market_days_lock = threading.Lock()


def _market_day_frame(pro: Any, api_name: str, day: Any) -> Optional[pd.DataFrame]:
    """Market-wide frame for one Shanghai date indexed by ts_code; None on failure."""
    trade_date = day.strftime("%Y%m%d")
    key = (api_name, trade_date)
    with _market_days_lock:
        cached = _market_days.get(key)
        if cached is not None:
            return cached
        failed_at = _market_day_failures.get(key)
        if failed_at is not None and time.monotonic() - failed_at < _MARKET_DAY_RETRY_SECONDS:
            return None
        try:
            frame = getattr(pro, api_name)(trade_date=trade_date)
        except Exception as exc:
            logger.info("Tushare %s market snapshot failed trade_date=%s: %s", api_name, trade_date, exc)
            _market_day_failures[key] = time.monotonic()
            return None
        if not isinstance(frame, pd.DataFrame) or frame.empty or "ts_code" not in frame.columns:
            frame = pd.DataFrame()
        else:
            frame = frame.drop_duplicates(subset=["ts_code"]).set_index("ts_code")
        # Today's snapshot is published after the close; an empty answer is only
        # final (a holiday) for past dates.
        if frame.empty and day >= datetime.now(_SHANGHAI).date():
            _market_day_failures[key] = time.monotonic()
            return frame
        _market_days[key] = frame
        _market_day_failures.pop(key, None)
        if len(_market_days) > _MARKET_DAY_CACHE_SIZE:
            for old in sorted(_market_days, key=lambda item: item[1])[: len(_market_days) - _MARKET_DAY_CACHE_SIZE]:
                _market_days.pop(old, None)
        return frame


def _market_window(
    pro: Any, ts_code: str, start: Any, end: Any
) -> Optional[tuple[Optional[pd.DataFrame], Dict[str, float]]]:
    """Daily rows and adj factors for ``ts_code`` from cached market snapshots.

    Returns None when the window is too long or any snapshot fails, so the
    caller falls back to the per-symbol request.
    """
    today = datetime.now(_SHANGHAI).date()
    end = min(end, today)
    if start > end or (end - start).days > _MARKET_WINDOW_MAX_DAYS:
        return None
    rows: list[dict[str, Any]] = []
    factors: Dict[str, float] = {}
    day = start
    while day <= end:
        if day.weekday() < 5:
            prices = _market_day_frame(pro, "daily", day)
            if prices is None:
                return None
            if not prices.empty:
                adj = _market_day_frame(pro, "adj_factor", day)
                if adj is None:
                    return None
                trade_date = day.strftime("%Y%m%d")
                if ts_code in prices.index:
                    row = prices.loc[ts_code].to_dict()
                    row["ts_code"] = ts_code
                    row["trade_date"] = trade_date
                    rows.append(row)
                if ts_code in adj.index:
                    try:
                        factor = float(adj.loc[ts_code, "adj_factor"])
                    except (TypeError, ValueError, KeyError):
                        factor = 0.0
                    if factor > 0:
                        factors[trade_date] = factor
        day += timedelta(days=1)
    return (pd.DataFrame(rows) if rows else None), factors


def _ashare_daily_meta(
    pro: Any,
    ts_code: str,
    prices: pd.DataFrame,
    start_date: str,
    end_date: Optional[str],
    *,
    factors: Optional[Dict[str, float]] = None,
) -> Dict[str, Dict[str, Any]]:
    """QFQ ratios plus limit/suspend flags.

    ``pro_bar`` is not available on the HTTP gateways, so forward adjustment is
    ``raw * adj_factor / latest_adj_factor``. Limit flags use ``stk_limit``
    prices when the gateway returns them, otherwise the board band.
    """
    if factors is None:
        window = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        factors = _factor_map(_optional_frame(pro, "adj_factor", **window))
    ratios = _qfq_ratios(prices, factors)
    recent = [_trade_date_key(v) for v in prices["trade_date"].tolist()][-_FLAG_RECENT_SESSIONS:]
    limits: Dict[str, tuple[float, float]] = {}
    suspended: set[str] = set()
    st_days: set[str] = set()
    for day in recent:
        snapshot = _market_day_flags(pro, day)
        if snapshot is None:
            continue
        if ts_code in snapshot["limits"]:
            limits[day] = snapshot["limits"][ts_code]
        if ts_code in snapshot["suspended"]:
            suspended.add(day)
        if ts_code in snapshot["st"]:
            st_days.add(day)

    meta: Dict[str, Dict[str, Any]] = {}
    for _, row in prices.iterrows():
        key = _trade_date_key(row.get("trade_date"))
        if len(key) != 8:
            continue
        try:
            close = float(row["close"])
        except (TypeError, ValueError, KeyError):
            continue
        up, down = limits.get(key, (0.0, 0.0))
        if up <= 0 or down <= 0:
            try:
                pre_close = float(row.get("pre_close") or 0.0)
            except (TypeError, ValueError):
                pre_close = 0.0
            if pre_close > 0:
                pct = _board_limit_pct(ts_code, st=key in st_days)
                up = round(pre_close * (1.0 + pct) + 1e-8, 2)
                down = round(pre_close * (1.0 - pct) + 1e-8, 2)
        meta[key] = {
            "ratio": ratios.get(key, 1.0),
            "limit_up": _at_up_limit(close, up),
            "limit_down": _at_down_limit(close, down),
            "suspended": key in suspended,
        }
    return meta


def _factor_map(frame: Optional[pd.DataFrame]) -> Dict[str, float]:
    if frame is None or "adj_factor" not in frame.columns:
        return {}
    out: Dict[str, float] = {}
    for _, row in frame.iterrows():
        try:
            factor = float(row["adj_factor"])
        except (TypeError, ValueError):
            continue
        if factor > 0:
            out[_trade_date_key(row.get("trade_date"))] = factor
    return out


def _qfq_ratios(prices: pd.DataFrame, factors: Dict[str, float]) -> Dict[str, float]:
    if not factors:
        return {}
    ordered = [_trade_date_key(value) for value in prices["trade_date"].tolist()]
    filled: list[float | None] = []
    last: float | None = None
    for key in ordered:
        factor = factors.get(key)
        if factor:
            last = factor
        filled.append(last)
    first = next((value for value in filled if value), None)
    if not first:
        return {}
    seen = first
    ratios: Dict[str, float] = {}
    latest = next((value for value in reversed(filled) if value), first)
    if latest <= 0:
        return {}
    for key, value in zip(ordered, filled):
        seen = value or seen
        ratios[key] = seen / latest
    return ratios
