#!/usr/bin/env python3
"""Convert yearly A-share 1-minute CSV zips into Parquet.

Input: ``<src>/<YYYY>_1min.zip`` containing ``sh600000_<YYYY>.csv`` files with
header ``时间,代码,名称,开盘价,收盘价,最高价,最低价,成交量,成交额,涨幅,振幅``.
Prices must be unadjusted; volume is in lots of 100 shares.

Output: ``<dst>/year=<YYYY>/bars.parquet`` sorted by (ts_code, ts) plus a
``manifest.json``.  The 09:30 bar is the opening call auction.

    python scripts/import_ashare_minute.py --src /src --dst /dst --years 2015-2025
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

SOURCE_COLUMNS = ["ts", "code", "name", "open", "close", "high", "low", "volume", "amount", "pct_chg", "amplitude"]
MEMBER_RE = re.compile(r"(?:^|/)(sh|sz|bj)(\d{6})_\d{4}\.csv$", re.IGNORECASE)
SCHEMA = pa.schema(
    [
        ("ts_code", pa.string()),
        ("ts", pa.timestamp("s")),
        ("trade_date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("amount", pa.float64()),
    ]
)
ROW_GROUP_ROWS = 4_000_000


def ts_code_for(member: str) -> str | None:
    match = MEMBER_RE.search(member)
    if not match:
        return None
    return f"{match.group(2)}.{match.group(1).upper()}"


def parse_member(payload: bytes, ts_code: str) -> pa.Table:
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    table = pacsv.read_csv(
        io.BytesIO(payload),
        read_options=pacsv.ReadOptions(column_names=SOURCE_COLUMNS, skip_rows=1),
        convert_options=pacsv.ConvertOptions(
            include_columns=["ts", "open", "close", "high", "low", "volume", "amount"],
            column_types={
                "ts": pa.timestamp("s"),
                "open": pa.float64(),
                "close": pa.float64(),
                "high": pa.float64(),
                "low": pa.float64(),
                "volume": pa.float64(),
                "amount": pa.float64(),
            },
        ),
    )
    table = table.filter(pc.is_valid(table["ts"]))
    ts = table["ts"]
    return pa.table(
        {
            "ts_code": pa.array([ts_code] * table.num_rows, pa.string()),
            "ts": ts,
            "trade_date": pc.cast(ts, pa.date32()),
            "open": table["open"],
            "high": table["high"],
            "low": table["low"],
            "close": table["close"],
            "volume": pc.multiply(table["volume"], 100.0),
            "amount": table["amount"],
        },
        schema=SCHEMA,
    ).sort_by([("ts", "ascending")])


def convert_year(src: Path, dst: Path, year: int, *, force: bool = False) -> dict:
    archive = src / f"{year}_1min.zip"
    out_dir = dst / f"year={year}"
    manifest_path = out_dir / "manifest.json"
    if not archive.is_file():
        return {"year": year, "status": "missing", "archive": str(archive)}
    stat = archive.stat()
    if manifest_path.is_file() and not force:
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("archive_size") == stat.st_size and manifest.get("status") == "ok":
            return {**manifest, "status": "skipped"}

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / "bars.parquet.tmp"
    started = time.time()
    rows = 0
    symbols = 0
    bad_members: list[str] = []
    pending: list[pa.Table] = []
    pending_rows = 0
    with zipfile.ZipFile(archive) as zf, pq.ParquetWriter(
        tmp_path, SCHEMA, compression="zstd", compression_level=3
    ) as writer:
        members = sorted(
            (code, info) for info in zf.infolist() if (code := ts_code_for(info.filename)) is not None
        )
        for code, info in members:
            try:
                table = parse_member(zf.read(info), code)
            except (pa.ArrowInvalid, ValueError) as exc:
                bad_members.append(f"{info.filename}: {str(exc)[:200]}")
                continue
            if table.num_rows == 0:
                continue
            pending.append(table)
            pending_rows += table.num_rows
            rows += table.num_rows
            symbols += 1
            if pending_rows >= ROW_GROUP_ROWS:
                writer.write_table(pa.concat_tables(pending), row_group_size=ROW_GROUP_ROWS)
                pending, pending_rows = [], 0
        if pending:
            writer.write_table(pa.concat_tables(pending), row_group_size=ROW_GROUP_ROWS)
    os.replace(tmp_path, out_dir / "bars.parquet")

    manifest = {
        "year": year,
        "status": "ok",
        "archive": archive.name,
        "archive_size": stat.st_size,
        "rows": rows,
        "symbols": symbols,
        "bad_members": bad_members,
        "parquet_bytes": (out_dir / "bars.parquet").stat().st_size,
        "seconds": round(time.time() - started, 1),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def parse_years(text: str) -> list[int]:
    years: list[int] = []
    for part in text.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            years.extend(range(int(start), int(end) + 1))
        elif part.strip():
            years.append(int(part))
    return years


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="directory with <YYYY>_1min.zip (unadjusted)")
    parser.add_argument("--dst", required=True)
    parser.add_argument("--years", default="2015-2025")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    failed = False
    for year in parse_years(args.years):
        result = convert_year(Path(args.src), Path(args.dst), year, force=args.force)
        failed |= result["status"] == "missing" or bool(result.get("bad_members"))
        print(json.dumps({k: v for k, v in result.items() if k != "bad_members"}
                         | {"bad_members": len(result.get("bad_members", []))}, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
