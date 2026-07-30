from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.utils.logger import get_logger

logger = get_logger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def is_tushare_configured() -> bool:
    return bool(str(os.environ.get("TUSHARE_TOKEN") or "").strip())


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


_pro_cache: Dict[tuple[str, str], Any] = {}


def _build_pro():
    import tushare as ts

    token = str(os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        return None
    http_url = str(os.environ.get("TUSHARE_HTTP_URL") or "").strip()
    cache_key = (token, http_url)
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
    """True when the newest bar is on today's Asia/Shanghai calendar date."""
    if not rows:
        return False
    try:
        last = max(int(r.get("time") or 0) for r in rows)
    except (TypeError, ValueError):
        return False
    if last <= 0:
        return False
    last_day = datetime.fromtimestamp(last, tz=_SHANGHAI).date()
    today = datetime.now(_SHANGHAI).date()
    return last_day >= today


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
    end_date = None
    if before_time:
        end_date = datetime.fromtimestamp(int(before_time), tz=timezone.utc).strftime("%Y%m%d")
    try:
        # 多取一点再截断，兼容 end_date 过滤
        df = pro.daily(ts_code=ts_code, end_date=end_date)
    except Exception as exc:
        logger.warning("Tushare daily failed ts_code=%s: %s", ts_code, exc)
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


def _pct_to_ratio(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    # Tushare fina_indicator ROE / YoY fields are percentages (e.g. 34.46).
    return number / 100.0


def fetch_tushare_fina_history(tencent_code: str) -> List[Dict[str, Any]]:
    """Point-in-time financial indicators for A-shares via Tushare fina_indicator."""
    if not is_tushare_configured():
        return []
    pro = _build_pro()
    if pro is None:
        return []
    ts_code = tencent_code_to_ts_code(tencent_code)
    fields = "ts_code,end_date,ann_date,roe,debt_to_eqt,or_yoy,tr_yoy"
    try:
        df = pro.fina_indicator(ts_code=ts_code, fields=fields)
    except Exception as exc:
        logger.warning("Tushare fina_indicator failed ts_code=%s: %s", ts_code, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    # Prefer the latest announcement row per report period.
    df = df.sort_values(["end_date", "ann_date"]).drop_duplicates("end_date", keep="last")
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        end_raw = str(row.get("end_date") or "").strip()
        ann_raw = str(row.get("ann_date") or "").strip() or end_raw
        if len(end_raw) != 8 or len(ann_raw) != 8:
            continue
        try:
            period_end = datetime.strptime(end_raw, "%Y%m%d").date()
            available_at = datetime.strptime(ann_raw, "%Y%m%d").date()
        except ValueError:
            continue
        growth = row.get("or_yoy")
        if growth is None or (isinstance(growth, float) and growth != growth):
            growth = row.get("tr_yoy")
        out.append(
            {
                "period_end": period_end,
                "available_at": available_at,
                "return_on_equity": _pct_to_ratio(row.get("roe")),
                "revenue_growth": _pct_to_ratio(growth),
                "debt_to_equity": _finite_float(row.get("debt_to_eqt")),
            }
        )
    return out


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number
