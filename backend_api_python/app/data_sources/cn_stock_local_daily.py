"""Local A-share daily OHLCV from per-symbol CSV folders (e.g. 后复权 dumps)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.data_sources.tencent import normalize_cn_code, parse_tencent_kline_time
from app.utils.logger import get_logger

logger = get_logger(__name__)

_OHLC_ALIASES = {
    "open": ("开盘价", "open", "Open"),
    "high": ("最高价", "high", "High"),
    "low": ("最低价", "low", "Low"),
    "close": ("收盘价", "close", "Close"),
    "volume": ("成交量（股）", "成交量", "volume", "Volume", "vol"),
    "date": ("日期", "date", "Date", "time", "datetime"),
}


def local_daily_root() -> Path | None:
    raw = str(os.environ.get("CNSTOCK_LOCAL_DAILY_DIR") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _symbol_to_code6(symbol: str) -> str:
    code = normalize_cn_code(symbol).upper()
    if code.startswith("SH") or code.startswith("SZ"):
        code = code[2:]
    if code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".SS"):
        code = code.split(".", 1)[0]
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _pick_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = {h.strip(): h for h in headers}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    # fuzzy: strip spaces
    normalized = {h.strip().replace(" ", ""): h for h in headers}
    for alias in aliases:
        key = alias.replace(" ", "")
        if key in normalized:
            return normalized[key]
    return None


@lru_cache(maxsize=1024)
def _load_csv_rows(path_str: str) -> tuple[dict[str, Any], ...]:
    import csv

    path = Path(path_str)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return tuple()
        headers = list(reader.fieldnames)
        date_col = _pick_column(headers, _OHLC_ALIASES["date"])
        open_col = _pick_column(headers, _OHLC_ALIASES["open"])
        high_col = _pick_column(headers, _OHLC_ALIASES["high"])
        low_col = _pick_column(headers, _OHLC_ALIASES["low"])
        close_col = _pick_column(headers, _OHLC_ALIASES["close"])
        volume_col = _pick_column(headers, _OHLC_ALIASES["volume"])
        if not all((date_col, open_col, high_col, low_col, close_col)):
            logger.warning("Local daily CSV missing OHLC columns: %s", path)
            return tuple()

        rows: list[dict[str, Any]] = []
        for item in reader:
            ts = parse_tencent_kline_time(str(item.get(date_col) or ""))
            if ts is None:
                continue
            try:
                o = float(item.get(open_col))
                h = float(item.get(high_col))
                low = float(item.get(low_col))
                c = float(item.get(close_col))
            except (TypeError, ValueError):
                continue
            vol = 0.0
            if volume_col:
                try:
                    vol = float(item.get(volume_col) or 0.0)
                except (TypeError, ValueError):
                    vol = 0.0
            rows.append(
                {
                    "time": int(ts),
                    "open": round(o, 4),
                    "high": round(h, 4),
                    "low": round(low, 4),
                    "close": round(c, 4),
                    "volume": round(vol, 2),
                }
            )
    rows.sort(key=lambda row: row["time"])
    return tuple(rows)


def resolve_local_daily_csv(symbol: str) -> Path | None:
    root = local_daily_root()
    if root is None:
        return None
    code6 = _symbol_to_code6(symbol)
    if not code6:
        return None
    candidate = root / f"{code6}.csv"
    return candidate if candidate.is_file() else None


def fetch_local_daily_klines(
    *,
    symbol: str,
    limit: int,
    before_time: Optional[int] = None,
    after_time: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return daily bars from local CSV when CNSTOCK_LOCAL_DAILY_DIR is configured."""
    path = resolve_local_daily_csv(symbol)
    if path is None:
        return []
    try:
        rows = list(_load_csv_rows(str(path.resolve())))
    except Exception as exc:
        logger.warning("Local daily CSV read failed %s: %s", path, exc)
        return []
    if not rows:
        return []

    if before_time is not None:
        rows = [row for row in rows if int(row["time"]) < int(before_time)]
    if after_time is not None:
        rows = [row for row in rows if int(row["time"]) >= int(after_time)]

    lim = max(int(limit or 1), 1)
    if len(rows) > lim:
        # Keep the right edge of the requested window (same idea as remote truncate=False).
        rows = rows[-lim:]
    return rows
