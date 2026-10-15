"""A-share 1-minute bars stored as yearly Parquet (see scripts/import_ashare_minute.py).

The vendor bars are built from ~3s snapshots: closes and volumes match the
exchange daily bar, but intraday extremes and the SZ opening auction can be
off by a tick.  Official open/high/low and limit touches therefore come from
``qd_ashare_daily_bar``; minute bars model the intraday path only.  Stocks
delisted before the vendor snapshot are absent, so callers must fall back to
daily bars rather than drop them from the universe.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

MINUTE_DATASET = "minute"
PRICE_TOLERANCE = 0.0051
VOLUME_TOLERANCE = 0.001
MAX_CLOSE_MISMATCH_RATE = 0.001
MAX_VOLUME_MISMATCH_RATE = 0.01

_DAILY_FROM_MINUTE_SQL = """
SELECT ts_code, trade_date,
       arg_min(open, ts) AS open, max(high) AS high, min(low) AS low,
       arg_max(close, ts) AS close, sum(volume) AS volume, sum(amount) AS amount
FROM read_parquet(?)
WHERE trade_date BETWEEN ? AND ?
GROUP BY ts_code, trade_date
HAVING sum(volume) > 0
"""


def minute_root() -> Path:
    return Path(os.getenv("ASHARE_MINUTE_DIR", "/data/ashare_minute"))


def _year_files(start: date, end: date, root: Path | None = None) -> list[str]:
    base = root or minute_root()
    files = [base / f"year={year}" / "bars.parquet" for year in range(start.year, end.year + 1)]
    return [str(path) for path in files if path.is_file()]


def load_minute_bars(
    ts_codes: Iterable[str],
    start: date,
    end: date,
    *,
    root: Path | None = None,
) -> pd.DataFrame:
    """Unadjusted 1-minute bars; suspended (all-zero-volume) days are dropped."""
    import duckdb

    codes = sorted({str(code).strip().upper() for code in ts_codes if code})
    files = _year_files(start, end, root)
    columns = ["ts_code", "ts", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    if not codes or not files:
        return pd.DataFrame(columns=columns)
    with duckdb.connect() as con:
        return con.execute(
            """
            SELECT * FROM read_parquet(?)
            WHERE ts_code IN (SELECT UNNEST(?)) AND trade_date BETWEEN ? AND ?
            QUALIFY sum(volume) OVER (PARTITION BY ts_code, trade_date) > 0
            ORDER BY ts_code, ts
            """,
            [files, codes, start, end],
        ).df()[columns]


def daily_from_minutes(start: date, end: date, *, root: Path | None = None) -> pd.DataFrame:
    import duckdb

    files = _year_files(start, end, root)
    if not files:
        return pd.DataFrame(columns=["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"])
    with duckdb.connect() as con:
        return con.execute(_DAILY_FROM_MINUTE_SQL, [files, start, end]).df()


def compare_minute_to_daily(minute_daily: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Per-trade-date agreement between minute aggregates and exchange daily bars."""
    traded = daily[daily["volume"].fillna(0) > 0]
    merged = traded.merge(
        minute_daily, on=["ts_code", "trade_date"], how="outer", suffixes=("", "_m"), indicator=True
    )
    both = merged["_merge"] == "both"
    volume_gap = (merged["volume_m"] - merged["volume"]).abs()
    merged["matched"] = both
    merged["missing"] = merged["_merge"] == "left_only"
    merged["extra"] = merged["_merge"] == "right_only"
    merged["close_bad"] = both & ((merged["close_m"] - merged["close"]).abs() > PRICE_TOLERANCE)
    merged["volume_bad"] = both & (volume_gap > (merged["volume"] * VOLUME_TOLERANCE).clip(lower=1.0))
    merged["open_bad"] = both & ((merged["open_m"] - merged["open"]).abs() > PRICE_TOLERANCE)
    summary = (
        merged.groupby("trade_date")[["matched", "missing", "extra", "close_bad", "volume_bad", "open_bad"]]
        .sum()
        .astype(int)
        .reset_index()
    )
    matched = summary["matched"].clip(lower=1)
    healthy = (summary["matched"] > 0) & (summary["close_bad"] / matched <= MAX_CLOSE_MISMATCH_RATE) & (
        summary["volume_bad"] / matched <= MAX_VOLUME_MISMATCH_RATE
    )
    summary["status"] = healthy.map({True: "ok", False: "mismatch"})
    return summary


def _load_daily(start: date, end: date) -> pd.DataFrame:
    from app.utils.db import get_db_connection

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT ts_code, trade_date, open, close, volume FROM qd_ashare_daily_bar"
            " WHERE trade_date BETWEEN ? AND ?",
            (start, end),
        )
        rows = cur.fetchall() or []
        cur.close()
    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "close", "volume"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    return frame


def run_minute_qa(start: date, end: date, *, root: Path | None = None) -> dict[str, Any]:
    """Compare minute aggregates with ``qd_ashare_daily_bar`` and log per trade date."""
    from app.utils.db import get_db_connection

    minute_daily = daily_from_minutes(start, end, root=root)
    minute_daily["trade_date"] = pd.to_datetime(minute_daily["trade_date"]).dt.date
    summary = compare_minute_to_daily(minute_daily, _load_daily(start, end))
    with get_db_connection() as db:
        cur = db.cursor()
        cur.executemany(
            """
            INSERT INTO qd_ashare_sync_log (trade_date, dataset, status, rows, detail)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (trade_date, dataset) DO UPDATE SET
              status = EXCLUDED.status, rows = EXCLUDED.rows,
              detail = EXCLUDED.detail, updated_at = NOW()
            """,
            [
                (
                    row.trade_date,
                    MINUTE_DATASET,
                    row.status,
                    int(row.matched),
                    f"missing={row.missing} extra={row.extra} close_bad={row.close_bad} "
                    f"volume_bad={row.volume_bad} open_bad={row.open_bad}",
                )
                for row in summary.itertuples()
            ],
        )
        db.commit()
        cur.close()
    return {
        "days": int(len(summary)),
        "mismatch_days": summary.loc[summary["status"] != "ok", "trade_date"].astype(str).tolist(),
        "missing_rows": int(summary["missing"].sum()),
        "open_bad_rows": int(summary["open_bad"].sum()),
    }
