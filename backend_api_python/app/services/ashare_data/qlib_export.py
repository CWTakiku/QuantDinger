"""Write Qlib CSV from unadjusted daily bars plus the adjustment factor."""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

_TS = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)


def qlib_symbol(ts_code: str) -> str | None:
    match = _TS.match(str(ts_code or "").strip().upper())
    if not match:
        return None
    return f"{match.group(2)}{match.group(1)}"


def write_symbol_csv(directory: str | Path, ts_code: str, rows: list[dict]) -> Path | None:
    symbol = qlib_symbol(ts_code)
    if not symbol or not rows:
        return None
    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{symbol}.csv"
    lines = ["date,open,high,low,close,volume,factor"]
    for row in rows:
        trade_date = str(row["trade_date"])[:10]
        lines.append(
            ",".join(
                [
                    trade_date,
                    str(row["open"]),
                    str(row["high"]),
                    str(row["low"]),
                    str(row["close"]),
                    str(int(row["volume"])),
                    str(row["adj_factor"]),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def refresh_instrument_end(path: str | Path, end: date | str) -> int:
    """Advance memberships that are still open as of the file's latest end date.

    A line that already ended earlier is a stock that left the universe. Only
    lines sharing the current maximum end move forward.
    """
    file = Path(path)
    tip = str(end)[:10]
    if not file.is_file() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tip):
        return 0
    rows = [line.split("\t") for line in file.read_text(encoding="utf-8").splitlines()]
    ends = [parts[2] for parts in rows if len(parts) == 3 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[2])]
    if not ends:
        return 0
    open_end = max(ends)
    if open_end >= tip:
        return 0
    changed = 0
    updated = []
    for parts in rows:
        line = "\t".join(parts)
        if len(parts) == 3 and parts[2] == open_end:
            parts[2] = tip
            line = "\t".join(parts)
            changed += 1
        updated.append(line)
    if changed:
        file.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return changed


def append_symbol_rows(directory: str | Path, ts_code: str, rows: list[dict]) -> Path | None:
    """Append bars to an existing Qlib CSV, creating the file when it is new."""
    symbol = qlib_symbol(ts_code)
    if not symbol or not rows:
        return None
    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{symbol}.csv"
    existing_dates: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            if line:
                existing_dates.add(line.split(",", 1)[0])
    fresh = [row for row in rows if str(row["trade_date"])[:10] not in existing_dates]
    if not path.is_file():
        return write_symbol_csv(dest, ts_code, fresh)
    if not fresh:
        return path
    extra = []
    for row in fresh:
        extra.append(
            ",".join(
                [
                    str(row["trade_date"])[:10],
                    str(row["open"]),
                    str(row["high"]),
                    str(row["low"]),
                    str(row["close"]),
                    str(int(row["volume"])),
                    str(row["adj_factor"]),
                ]
            )
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(extra) + "\n")
    return path


def export_range(directory: str | Path, start: str, end: str, on_progress=None) -> dict:
    """Write one CSV per symbol for a date range. Each file is written once."""
    from app.utils.db import get_db_connection

    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    files = 0
    rows_written = 0
    handle = None
    current = None
    with get_db_connection() as db:
        raw = db._conn
        cur = raw.cursor(name="qlib_export_range")
        cur.itersize = 50000
        cur.execute(
            """
            SELECT ts_code,
                   to_char(trade_date, 'YYYY-MM-DD'),
                   open, high, low, close, volume, adj_factor
            FROM qd_ashare_daily_bar
            WHERE trade_date BETWEEN %s AND %s
            ORDER BY ts_code, trade_date
            """,
            (str(start)[:10], str(end)[:10]),
        )
        try:
            for row in cur:
                symbol = qlib_symbol(row[0])
                if symbol is None:
                    continue
                if symbol != current:
                    if handle is not None:
                        handle.close()
                    current = symbol
                    handle = (dest / f"{symbol}.csv").open("w", encoding="utf-8", buffering=1024 * 1024)
                    handle.write("date,open,high,low,close,volume,factor\n")
                    files += 1
                    if on_progress is not None and files % 200 == 0:
                        on_progress(files, rows_written, symbol)
                handle.write(
                    f"{row[1]},{row[2]},{row[3]},{row[4]},{row[5]},{int(row[6])},{row[7]}\n"
                )
                rows_written += 1
        finally:
            if handle is not None:
                handle.close()
            cur.close()
            raw.rollback()
    if on_progress is not None:
        on_progress(files, rows_written, current or "")
    return {"files": files, "rows": rows_written}


def export_trade_dates(directory: str | Path, trade_dates: list) -> int:
    """Append one or more stored trade dates into per-symbol Qlib CSV files."""
    if not trade_dates:
        return 0
    from app.utils.db import get_db_connection

    dates = [str(day)[:10] for day in trade_dates]
    placeholders = ",".join("?" for _ in dates)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT ts_code, trade_date, open, high, low, close, volume, adj_factor
            FROM qd_ashare_daily_bar
            WHERE trade_date IN ({placeholders})
            ORDER BY ts_code, trade_date
            """,
            tuple(dates),
        )
        rows = cur.fetchall() or []
        cur.close()
    written = 0
    bucket: list[dict] = []
    current = None
    for row in rows:
        item = dict(row)
        code = item["ts_code"]
        if current is None:
            current = code
        if code != current:
            if append_symbol_rows(directory, current, bucket):
                written += 1
            bucket = []
            current = code
        bucket.append(item)
    if current and append_symbol_rows(directory, current, bucket):
        written += 1
    return written


def export_configured_trade_dates(trade_dates: list) -> int:
    directory = os.getenv("ASHARE_QLIB_CSV_DIR", "").strip()
    if not directory:
        return 0
    return export_trade_dates(directory, trade_dates)


def refresh_configured_instrument_end(end: date | str) -> int:
    path = os.getenv("ASHARE_QLIB_INSTRUMENTS", "").strip()
    if not path:
        return 0
    return refresh_instrument_end(path, end)
