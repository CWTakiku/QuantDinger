"""A-share PE-TTM and rolling percentile from Tushare daily_basic store."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from app.data_sources.tushare_cn import _build_pro, is_tushare_configured, tencent_code_to_ts_code
from app.services.csi300_enhanced.tushare_sync import normalize_daily_basic_frame, persist_daily_basic
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 756  # ~3y trading calendar span


def _as_date(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def platform_to_ts_code(symbol: object) -> str:
    text = str(symbol or "").strip()
    if text.upper().startswith("CNSTOCK:"):
        text = text.split(":", 1)[1]
    if "@" in text:
        text = text.split("@", 1)[0]
    return tencent_code_to_ts_code(text)


def _load_pe_series(ts_code: str, start: date, end: date) -> pd.Series:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT trade_date, pe_ttm
            FROM qd_ashare_daily_basic
            WHERE ts_code = ?
              AND trade_date >= ?
              AND trade_date <= ?
              AND pe_ttm IS NOT NULL
            ORDER BY trade_date
            """,
            (ts_code, start, end),
        )
        rows = cur.fetchall() or []
    if not rows:
        return pd.Series(dtype=float)
    index = [row["trade_date"] if isinstance(row["trade_date"], date) else date.fromisoformat(str(row["trade_date"])[:10]) for row in rows]
    values = [float(row["pe_ttm"]) for row in rows]
    series = pd.Series(values, index=pd.Index(index), dtype=float)
    series = series[(series > 0) & series.notna()]
    return series


def _fetch_and_persist_pe_history(ts_code: str, start: date, end: date) -> int:
    if not is_tushare_configured():
        return 0
    pro = _build_pro()
    if pro is None:
        return 0
    try:
        frame = pro.daily_basic(
            ts_code=ts_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,dv_ttm,turnover_rate,volume_ratio,total_mv,circ_mv",
        )
    except Exception as exc:
        logger.warning("tushare daily_basic history failed ts_code=%s: %s", ts_code, exc)
        return 0
    if frame is None or getattr(frame, "empty", True):
        return 0
    return int(persist_daily_basic(normalize_daily_basic_frame(frame)) or 0)


def compute_pe_percentile(
    *,
    symbol: object,
    as_of: date | str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    autofetch: bool = True,
) -> dict[str, Any]:
    """Return pe_ttm and rolling percentile in [0, 1] for ``symbol`` as of ``as_of``.

    Percentile is the fraction of lookback pe_ttm values that are ``<=`` current.
    Missing data returns ``pe_ttm=None`` / ``pe_percentile=None`` (callers should skip gates).
    """
    as_of_d = _as_date(as_of)
    lookback = max(20, int(lookback_days or DEFAULT_LOOKBACK_DAYS))
    start = as_of_d - timedelta(days=lookback)
    ts_code = platform_to_ts_code(symbol)
    series = _load_pe_series(ts_code, start, as_of_d)
    if series.empty and autofetch:
        try:
            _fetch_and_persist_pe_history(ts_code, start, as_of_d)
        except Exception as exc:
            logger.warning("pe history autofetch failed ts_code=%s: %s", ts_code, exc)
        series = _load_pe_series(ts_code, start, as_of_d)
    if series.empty:
        return {
            "symbol": str(symbol),
            "ts_code": ts_code,
            "as_of": as_of_d.isoformat(),
            "pe_ttm": None,
            "pe_percentile": None,
            "samples": 0,
        }
    # PIT: only observations on or before as_of
    series = series[series.index <= as_of_d]
    if series.empty:
        return {
            "symbol": str(symbol),
            "ts_code": ts_code,
            "as_of": as_of_d.isoformat(),
            "pe_ttm": None,
            "pe_percentile": None,
            "samples": 0,
        }
    current = float(series.iloc[-1])
    percentile = float((series <= current).sum() / len(series))
    return {
        "symbol": str(symbol),
        "ts_code": ts_code,
        "as_of": as_of_d.isoformat(),
        "pe_ttm": current,
        "pe_percentile": percentile,
        "samples": int(len(series)),
    }
