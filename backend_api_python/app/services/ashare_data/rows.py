"""Pure transforms from raw Tushare frames to ``qd_ashare_*`` rows."""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime
from typing import Any, Iterable, Mapping

import pandas as pd

# Tushare daily: vol in lots of 100 shares, amount in thousand CNY.
# daily_basic: total_mv / circ_mv in ten-thousand CNY.
_VOLUME_MULTIPLIER = 100.0
_AMOUNT_MULTIPLIER = 1000.0
_MV_MULTIPLIER = 10000.0

MIN_DAILY_COVERAGE = 0.99
MIN_BASIC_COVERAGE = 0.97
CROSSED_CLOSE_GAP = 0.01

BAR_COLUMNS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "up_limit",
    "down_limit",
    "is_st",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "total_mv",
    "circ_mv",
    "pe_ttm",
    "pb",
)


def parse_ymd(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    try:
        return datetime.strptime(text[:10].replace("-", ""), "%Y%m%d").date()
    except ValueError:
        return None


def _num(value: Any, multiplier: float = 1.0) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number * multiplier


def _frame(frames: Mapping[str, Any], name: str) -> pd.DataFrame:
    frame = frames.get(name)
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    return frame


def full_day_suspended_codes(suspend: pd.DataFrame) -> set[str]:
    if suspend.empty or "ts_code" not in suspend.columns:
        return set()
    kind = suspend.get("suspend_type", pd.Series("S", index=suspend.index)).fillna("S").astype(str)
    timing = suspend.get("suspend_timing", pd.Series("", index=suspend.index)).fillna("").astype(str)
    mask = (kind.str.upper() == "S") & (timing.str.strip() == "")
    return set(suspend.loc[mask, "ts_code"].astype(str))


def _exclude_bj(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return frame
    return frame[~frame["ts_code"].astype(str).str.upper().str.endswith(".BJ")]


def check_day_completeness(frames: Mapping[str, Any]) -> tuple[bool, str]:
    """Cross-check ``daily`` against the independently served ``daily_basic``.

    ``suspend_d`` misses many halts (e.g. ~1000 stocks halted in July 2015 but
    only ~180 listed), so ``stk_limit - suspend_d`` is reported, not enforced.
    """
    # daily carries NEEQ/BSE (.BJ) names that daily_basic omitted until ~2021-11.
    daily = _exclude_bj(_frame(frames, "daily"))
    limits = _frame(frames, "stk_limit")
    basic = _exclude_bj(_frame(frames, "daily_basic"))
    suspended = full_day_suspended_codes(_frame(frames, "suspend_d"))
    if daily.empty:
        return False, "daily empty"
    if limits.empty:
        return False, "stk_limit empty"
    if basic.empty:
        return False, "daily_basic empty"
    expected = len(set(limits["ts_code"].astype(str)) - suspended)
    detail = (
        f"daily={len(daily)} daily_basic={len(basic)} stk_limit={len(limits)} "
        f"suspended={len(suspended)} listed_minus_suspended={expected}"
    )
    if len(daily) < MIN_DAILY_COVERAGE * len(basic):
        return False, f"daily coverage {len(daily) / len(basic):.3f} of daily_basic; {detail}"
    if len(basic) < MIN_BASIC_COVERAGE * len(daily):
        return False, f"daily_basic coverage {len(basic) / len(daily):.3f} of daily; {detail}"
    crossed = close_mismatch_codes(daily, basic)
    if crossed:
        return False, f"daily/daily_basic close mismatch {len(crossed)}: {','.join(crossed[:5])}; {detail}"
    return True, detail


def close_mismatch_codes(daily: pd.DataFrame, basic: pd.DataFrame) -> list[str]:
    """Codes whose close differs between the two endpoints.

    The relay has been seen serving another stock's bar under a code, which
    row counts cannot detect; the two endpoints are served independently.
    Tushare's own daily_basic close is occasionally a few ticks off daily, so
    only gaps beyond ``CROSSED_CLOSE_GAP`` count.
    """
    if "close" not in daily.columns or "close" not in basic.columns:
        return []
    merged = daily[["ts_code", "close"]].merge(basic[["ts_code", "close"]], on="ts_code", suffixes=("", "_b"))
    close = pd.to_numeric(merged["close"], errors="coerce")
    reference = pd.to_numeric(merged["close_b"], errors="coerce")
    gap = (close - reference).abs() / reference.where(reference > 0)
    return sorted(merged.loc[gap > CROSSED_CLOSE_GAP, "ts_code"].astype(str))


def _indexed(frame: pd.DataFrame) -> dict[str, Mapping[str, Any]]:
    if frame.empty or "ts_code" not in frame.columns:
        return {}
    return {str(row["ts_code"]): row for row in frame.to_dict("records")}


def build_bar_rows(
    trade_date: date,
    frames: Mapping[str, Any],
    st_codes: Iterable[str] = (),
) -> list[tuple[Any, ...]]:
    """Merge daily + stk_limit + daily_basic into rows ordered as ``BAR_COLUMNS``."""
    daily = _frame(frames, "daily")
    if daily.empty:
        return []
    limits = _indexed(_frame(frames, "stk_limit"))
    basics = _indexed(_frame(frames, "daily_basic"))
    st = set(st_codes) | set(_frame(frames, "stock_st").get("ts_code", pd.Series(dtype=str)).astype(str))
    rows: list[tuple[Any, ...]] = []
    for bar in daily.to_dict("records"):
        code = str(bar.get("ts_code") or "").strip().upper()
        if not code:
            continue
        limit = limits.get(code, {})
        basic = basics.get(code, {})
        rows.append(
            (
                code,
                trade_date,
                _num(bar.get("open")),
                _num(bar.get("high")),
                _num(bar.get("low")),
                _num(bar.get("close")),
                _num(bar.get("pre_close")),
                _num(bar.get("vol"), _VOLUME_MULTIPLIER),
                _num(bar.get("amount"), _AMOUNT_MULTIPLIER),
                _num(limit.get("up_limit")),
                _num(limit.get("down_limit")),
                code in st,
                _num(basic.get("turnover_rate")),
                _num(basic.get("turnover_rate_f")),
                _num(basic.get("volume_ratio")),
                _num(basic.get("total_mv"), _MV_MULTIPLIER),
                _num(basic.get("circ_mv"), _MV_MULTIPLIER),
                _num(basic.get("pe_ttm")),
                _num(basic.get("pb")),
            )
        )
    return rows


def build_suspension_rows(trade_date: date, suspend: Any) -> list[tuple[Any, ...]]:
    frame = suspend if suspend is not None else pd.DataFrame()
    if getattr(frame, "empty", True):
        return []
    rows = []
    for item in frame.to_dict("records"):
        code = str(item.get("ts_code") or "").strip().upper()
        if not code:
            continue
        timing = item.get("suspend_timing")
        rows.append(
            (
                code,
                trade_date,
                str(item.get("suspend_type") or "")[:8],
                "" if timing is None or (isinstance(timing, float) and math.isnan(timing)) else str(timing)[:64],
            )
        )
    return rows


def is_st_name(name: str) -> bool:
    return "ST" in str(name or "").upper()


def build_st_intervals(name_rows: Iterable[Mapping[str, Any]]) -> dict[str, list[tuple[date, date | None]]]:
    """``{ts_code: sorted [(start, end_or_None)]}`` for names containing ST."""
    out: dict[str, list[tuple[date, date | None]]] = {}
    for row in name_rows:
        if not is_st_name(row.get("name")):
            continue
        start = parse_ymd(row.get("start_date"))
        if start is None:
            continue
        code = str(row.get("ts_code") or "").strip().upper()
        out.setdefault(code, []).append((start, parse_ymd(row.get("end_date"))))
    for intervals in out.values():
        intervals.sort(key=lambda item: item[0])
    return out


def st_codes_on(intervals: Mapping[str, list[tuple[date, date | None]]], day: date) -> set[str]:
    codes = set()
    for code, spans in intervals.items():
        idx = bisect_right([start for start, _ in spans], day)
        for start, end in spans[:idx]:
            if end is None or end >= day:
                codes.add(code)
                break
    return codes
