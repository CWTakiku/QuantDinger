"""Load point-in-time CSI500 bars, size, and the index level."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.utils.db import get_db_connection

CACHE_VERSION = "v1"


def load_research_inputs(
    start: date,
    end: date,
    *,
    universe_code: str = "csi500",
    history_start: date = date(2017, 1, 1),
) -> tuple[dict[str, pd.DataFrame], list[dict], pd.Series, str]:
    cached = _read_cache(history_start, end, universe_code)
    if cached is not None:
        print("using cached research panels", flush=True)
        return cached
    members = _members(universe_code)
    bars = _panel_query(
        """
        SELECT b.ts_code, b.trade_date, b.open, b.close, b.amount, b.turnover_rate,
               b.adj_factor, b.up_limit, b.down_limit, b.is_st, b.volume
        FROM qd_ashare_daily_bar b
        WHERE b.trade_date >= ? AND b.trade_date <= ?
          AND b.ts_code IN (
            SELECT DISTINCT m.symbol
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = ?
          )
        """,
        history_start,
        end,
        universe_code,
    )
    size = _panel_query(
        """
        SELECT b.ts_code, b.trade_date, b.circ_mv
        FROM qd_ashare_daily_basic b
        WHERE b.trade_date >= ? AND b.trade_date <= ?
          AND b.ts_code IN (
            SELECT DISTINCT m.symbol
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = ?
          )
        """,
        max(history_start, date(2021, 8, 31)),
        end,
        universe_code,
    )
    frames = _frames(bars, size)
    benchmark, benchmark_name = load_index_close(history_start, end)
    _write_cache(frames, members, benchmark, benchmark_name, history_start, end, universe_code)
    del start
    return frames, members, benchmark, benchmark_name


def _cache_dir() -> Path | None:
    raw = os.environ.get("FACTOR_BOOK_CACHE", "").strip()
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_cache(history_start: date, end: date, universe_code: str):
    cache = _cache_dir()
    marker = None if cache is None else cache / "version.txt"
    if cache is None or marker is None or not marker.is_file():
        return None
    if marker.read_text(encoding="utf-8").strip() != f"{CACHE_VERSION}|{universe_code}|{history_start}|{end}":
        return None
    frames = {}
    for path in sorted(cache.glob("frame_*.parquet")):
        frames[path.stem.removeprefix("frame_")] = pd.read_parquet(path)
    members = pd.read_parquet(cache / "members.parquet")
    benchmark = pd.read_parquet(cache / "benchmark.parquet")["close"]
    name = (cache / "benchmark_name.txt").read_text(encoding="utf-8").strip()
    rows = members.to_dict(orient="records")
    return frames, rows, benchmark, name


def _write_cache(frames, members, benchmark, benchmark_name, history_start, end, universe_code) -> None:
    cache = _cache_dir()
    if cache is None:
        return
    try:
        for name, frame in frames.items():
            frame.to_parquet(cache / f"frame_{name}.parquet")
        pd.DataFrame(members).to_parquet(cache / "members.parquet")
        benchmark.rename("close").to_frame().to_parquet(cache / "benchmark.parquet")
        (cache / "benchmark_name.txt").write_text(benchmark_name, encoding="utf-8")
        (cache / "version.txt").write_text(
            f"{CACHE_VERSION}|{universe_code}|{history_start}|{end}",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"cache skipped: {exc}", flush=True)


def load_index_close(start: date, end: date) -> tuple[pd.Series, str]:
    """Prefer the CSI500 total-return index. Fall back to the price index."""
    from app.data_sources.tushare_cn import _build_pro, _fetch_range

    pro = _build_pro()
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    for code, name in (("H00905.CSI", "中证500全收益"), ("000905.SH", "中证500价格指数")):
        frame = _fetch_range(pro, "index_daily", ts_code=code, start_date=start_text, end_date=end_text)
        if frame is None or getattr(frame, "empty", True) or "close" not in frame.columns:
            continue
        series = _index_series(frame)
        if len(series) > 100:
            return series, name
    return pd.Series(dtype=float), ""


def _index_series(frame: pd.DataFrame) -> pd.Series:
    copy = frame.copy()
    copy["trade_date"] = pd.to_datetime(copy["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    copy["close"] = pd.to_numeric(copy["close"], errors="coerce")
    series = copy.dropna(subset=["trade_date", "close"]).drop_duplicates("trade_date").set_index("trade_date")["close"]
    return series.sort_index().astype(float)


def _members(universe_code: str) -> list[dict]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT m.symbol, m.valid_from, m.valid_to
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = ?
            """,
            (universe_code,),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()
    return rows


def _panel_query(sql: str, start: date, end: date, universe_code: str) -> pd.DataFrame:
    frames = []
    cursor = date(start.year, 1, 1) if start.month != 1 or start.day != 1 else start
    if cursor < start:
        cursor = start
    while cursor <= end:
        year_end = min(end, date(cursor.year, 12, 31))
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(sql, (cursor, year_end, universe_code))
            rows = cur.fetchall() or []
            cur.close()
        if rows:
            frames.append(pd.DataFrame([dict(row) for row in rows]))
        print(f"loaded {cursor.isoformat()}..{year_end.isoformat()} rows={len(rows)}", flush=True)
        cursor = date(year_end.year + 1, 1, 1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _frames(bars: pd.DataFrame, size: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if bars.empty:
        raise RuntimeError("qd_ashare_daily_bar has no CSI500 rows in the requested window")
    bars = bars.copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    for column in ("open", "close", "amount", "turnover_rate", "adj_factor", "up_limit", "down_limit", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars["is_st"] = bars["is_st"].fillna(False).astype(bool)
    close = _pivot(bars, "close")
    factor = _pivot(bars, "adj_factor")
    adj_close = close * factor
    adj_close = adj_close.where(factor.notna(), close)
    frames = {
        "adj_close": adj_close,
        "raw_open": _pivot(bars, "open"),
        "raw_close": close,
        "amount": _pivot(bars, "amount"),
        "turnover": _pivot(bars, "turnover_rate"),
        "adj_factor": factor,
        "up_limit": _pivot(bars, "up_limit"),
        "down_limit": _pivot(bars, "down_limit"),
        "is_st": _pivot(bars, "is_st").fillna(False).astype(bool),
        "volume": _pivot(bars, "volume"),
    }
    if not size.empty:
        size = size.copy()
        size["trade_date"] = pd.to_datetime(size["trade_date"])
        size["circ_mv"] = pd.to_numeric(size["circ_mv"], errors="coerce")
        circ = _pivot(size, "circ_mv")
        # Market cap is stored on a handful of sessions. Carry the last
        # observation forward; do not fill backward.
        frames["log_mcap"] = np.log(circ.where(circ > 0)).sort_index().ffill()
    return frames


def _pivot(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    wide = frame.pivot_table(index="trade_date", columns="ts_code", values=column, aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()
