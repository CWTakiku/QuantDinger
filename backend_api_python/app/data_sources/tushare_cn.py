from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


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
    dt = datetime.strptime(str(trade_date), "%Y%m%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


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
