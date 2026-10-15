#!/usr/bin/env python3
"""Load index_weight history and rebuild one universe as point-in-time members.

    python scripts/import_csi_index_weights.py --index 000905.SH --universe csi500 --start 20150101 --end 20261011

Months already stored are skipped. A local pack file is loaded first when
``--pack`` points at ``index_weight__index_code=000905.SH.parquet``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from app.services.ashare_data.index_members import (  # noqa: E402
    as_date,
    boards_to_member_rows,
    canonicalize_cn_symbol,
)
from app.utils.db import get_db_connection  # noqa: E402


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        if month == 12:
            nxt = date(year + 1, 1, 1)
        else:
            nxt = date(year, month + 1, 1)
        window_start = max(start, date(year, month, 1))
        window_end = min(end, date.fromordinal(nxt.toordinal() - 1))
        windows.append((window_start, window_end))
        year, month = nxt.year, nxt.month
    return windows


def _existing_dates(index_code: str) -> set[date]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT DISTINCT trade_date FROM qd_ashare_index_weight WHERE index_code = ?",
            (index_code,),
        )
        found = {as_date(row["trade_date"]) for row in cur.fetchall() or []}
        cur.close()
    return found


def _store_frame(index_code: str, frame: pd.DataFrame, *, source: str) -> int:
    if frame is None or frame.empty:
        return 0
    rows = []
    for record in frame.to_dict("records"):
        code = canonicalize_cn_symbol(str(record.get("con_code") or ""))
        try:
            weight = float(record.get("weight"))
        except (TypeError, ValueError):
            continue
        if not code or weight <= 0 or weight != weight:
            continue
        rows.append((index_code, as_date(record["trade_date"]), code, weight, source))
    if not rows:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for index_code, trade_date, code, weight, source in rows:
            cur.execute(
                """
                INSERT INTO qd_ashare_index_weight
                    (index_code, trade_date, con_code, weight, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (index_code, trade_date, con_code, source)
                DO UPDATE SET weight = EXCLUDED.weight, ingested_at = NOW()
                """,
                (index_code, trade_date, code, weight, source),
            )
        db.commit()
        cur.close()
    return len(rows)


def _load_pack(path: Path, index_code: str) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if "index_code" in frame.columns:
        frame = frame[frame["index_code"].astype(str) == index_code]
    keep = [column for column in ("trade_date", "con_code", "weight") if column in frame.columns]
    return frame[keep].copy()


def _fetch_month(pro, index_code: str, start: date, end: date) -> pd.DataFrame:
    frame = pro.index_weight(
        index_code=index_code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    if "con_code" not in frame.columns and "con_ts_code" in frame.columns:
        frame = frame.rename(columns={"con_ts_code": "con_code"})
    keep = [column for column in ("trade_date", "con_code", "weight") if column in frame.columns]
    return frame[keep].copy()


def rebuild_universe(index_code: str, universe_code: str) -> dict:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_universes WHERE code = ?", (universe_code,))
        universe = cur.fetchone()
        if not universe:
            raise RuntimeError(f"universe not found: {universe_code}")
        universe_id = int(universe["id"])
        cur.execute(
            """
            SELECT trade_date, con_code, weight
            FROM qd_ashare_index_weight
            WHERE index_code = ?
            ORDER BY trade_date, con_code
            """,
            (index_code,),
        )
        raw = list(cur.fetchall() or [])
        boards: dict[date, dict[str, float]] = {}
        for row in raw:
            day = as_date(row["trade_date"])
            code = canonicalize_cn_symbol(str(row["con_code"]))
            try:
                weight = float(row["weight"])
            except (TypeError, ValueError):
                continue
            slot = boards.setdefault(day, {})
            if code not in slot or weight > slot[code]:
                slot[code] = weight
        members = boards_to_member_rows(boards)
        cur.execute("DELETE FROM qd_universe_members WHERE universe_id = ?", (universe_id,))
        version = ""
        if members:
            version = f"index_weight_pit:{index_code}:{members[0]['valid_from'].isoformat()}:{members[-1]['valid_from'].isoformat()}"
        for row in members:
            cur.execute(
                """
                INSERT INTO qd_universe_members (
                    universe_id, market, symbol, name, market_type,
                    valid_from, valid_to, member_weight, member_rank,
                    source_version, metadata_json
                )
                VALUES (?, 'CNStock', ?, '', 'spot', ?, ?, ?, ?, ?, '{}'::jsonb)
                """,
                (
                    universe_id,
                    row["symbol"],
                    row["valid_from"],
                    row["valid_to"],
                    row["weight"],
                    row["rank"],
                    version,
                ),
            )
        db.commit()
        cur.close()
    first = members[0]["valid_from"].isoformat() if members else None
    last = members[-1]["valid_from"].isoformat() if members else None
    return {
        "universe_id": universe_id,
        "boards": len(boards),
        "members": len(members),
        "first": first,
        "last": last,
    }


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="000905.SH")
    parser.add_argument("--universe", default="csi500")
    parser.add_argument("--start", default="20150101")
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--pack", default="")
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--skip-rebuild", action="store_true")
    args = parser.parse_args()
    index_code = args.index.strip().upper()
    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()

    if args.pack:
        stored = _store_frame(index_code, _load_pack(Path(args.pack), index_code), source="tushare_pack")
        print(f"pack rows={stored}", flush=True)

    if not args.skip_fetch:
        from app.data_sources.tushare_cn import _build_pro

        pro = _build_pro()
        if pro is None:
            raise RuntimeError("Tushare client is not configured")
        have = _existing_dates(index_code)
        for window_start, window_end in _month_windows(start, end):
            if any(window_start <= day <= window_end for day in have):
                continue
            try:
                frame = _fetch_month(pro, index_code, window_start, window_end)
            except Exception as exc:
                print(f"WARN {window_start} {exc}", flush=True)
                time.sleep(args.sleep)
                continue
            stored = _store_frame(index_code, frame, source="tushare")
            if stored:
                have.update(as_date(value) for value in frame["trade_date"].tolist())
            print(f"{window_start.isoformat()} rows={stored}", flush=True)
            time.sleep(args.sleep)

    if not args.skip_rebuild:
        print(rebuild_universe(index_code, args.universe), flush=True)


if __name__ == "__main__":
    main()
