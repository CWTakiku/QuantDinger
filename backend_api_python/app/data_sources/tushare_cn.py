from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

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
    end_date = end_dt.strftime("%Y%m%d") if before_time else None
    # Bound the window (≈1.6 calendar days per session + holiday slack) instead
    # of pulling a symbol's entire history on every call.
    start_date = (end_dt - timedelta(days=int(lim * 1.6) + 30)).strftime("%Y%m%d")
    use_index = is_ashare_index_ts_code(ts_code)
    api_name = "index_daily" if use_index else "daily"
    try:
        api = getattr(pro, api_name)
        df = api(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.warning("Tushare %s failed ts_code=%s: %s", api_name, ts_code, exc)
        return []
    # Index codes occasionally mis-routed: retry the other endpoint once.
    if (df is None or getattr(df, "empty", True)) and not use_index and ts_code.endswith(".SH"):
        try:
            df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
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
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            out.append(
                {
                    "time": _trade_date_to_unix(row["trade_date"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    # Tushare vol 单位为手；保持数值即可，与现网一致即可用
                    "volume": float(row.get("vol") or row.get("volume") or 0.0),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return out
