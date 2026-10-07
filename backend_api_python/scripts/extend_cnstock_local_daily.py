#!/usr/bin/env python3
"""Append Tushare daily bars onto local 后复权 CNStock CSV dumps.

Splices new OHLCV onto each CSV using the last local close as anchor so the
series stays continuous (vendor 后复权 ≠ raw Tushare hfq).

Usage (inside backend container, cwd=/app):
  python scripts/extend_cnstock_local_daily.py --end 20260807
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_sources.tushare_cn import _build_pro  # noqa: E402


def _code6_from_ts(ts_code: str) -> str:
    return str(ts_code).split(".", 1)[0].zfill(6)


def _tail_last_line(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        pos = handle.tell()
        if pos == 0:
            return ""
        buf = b""
        while pos > 0:
            step = min(4096, pos)
            pos -= step
            handle.seek(pos)
            chunk = handle.read(step)
            buf = chunk + buf
            if buf.count(b"\n") >= 2 or pos == 0:
                break
        lines = buf.splitlines()
        if not lines:
            return ""
        # skip trailing empty
        i = len(lines) - 1
        while i >= 0 and not lines[i].strip():
            i -= 1
        return lines[i].decode("utf-8-sig", errors="ignore") if i >= 0 else ""


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or [])


def _parse_last_row(path: Path, fieldnames: list[str]) -> tuple[str, dict[str, str]] | tuple[None, None]:
    line = _tail_last_line(path)
    if not line or line.startswith("日期"):
        return None, None
    # DictReader-compatible parse
    row = next(csv.DictReader([",".join(fieldnames), line]))
    last_date = str(row.get("日期") or "").strip()
    if not last_date:
        return None, None
    return last_date.replace("-", ""), row


def _fetch_trade_dates(pro, start: str, end: str) -> list[str]:
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if cal is None or cal.empty:
        return []
    return sorted(str(x) for x in cal["cal_date"].tolist())


def _fetch_day(pro, trade_date: str, sleep_s: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = pro.daily(trade_date=trade_date)
    time.sleep(sleep_s)
    adj = pro.adj_factor(trade_date=trade_date)
    time.sleep(sleep_s)
    if daily is None:
        daily = pd.DataFrame()
    if adj is None:
        adj = pd.DataFrame()
    return daily, adj


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=os.environ.get("CNSTOCK_LOCAL_DAILY_DIR", "/data/cnstock_daily"),
    )
    parser.add_argument("--end", default="20260807", help="YYYYMMDD inclusive")
    parser.add_argument("--sleep", type=float, default=0.12)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--anchor-last",
        default="",
        help="Only extend symbols whose local last date equals this YYYYMMDD "
        "(default: auto = mode of last dates)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"root missing: {root}", file=sys.stderr)
        return 2

    pro = _build_pro()
    if pro is None:
        print("Tushare not configured", file=sys.stderr)
        return 2

    files = sorted(root.glob("*.csv"))
    if not files:
        print("no csv", file=sys.stderr)
        return 2

    fieldnames = _read_header(files[0])
    last_by_code: dict[str, str] = {}
    template_by_code: dict[str, dict[str, str]] = {}
    path_by_code: dict[str, Path] = {}
    ends = Counter()

    for path in files:
        code = path.stem.zfill(6)
        last_date, last_row = _parse_last_row(path, fieldnames)
        if not last_date or not last_row:
            continue
        last_by_code[code] = last_date
        template_by_code[code] = last_row
        path_by_code[code] = path
        ends[last_date] += 1

    mode_last, mode_n = ends.most_common(1)[0]
    anchor_last = str(args.anchor_last or mode_last)
    print(
        f"csv_files={len(files)} symbols={len(last_by_code)} "
        f"mode_last={mode_last}({mode_n}) anchor_last={anchor_last}"
    )

    # Main dump cohort only (same last date). Newer outliers left alone.
    targets = {c: d for c, d in last_by_code.items() if d == anchor_last}
    print(f"target_symbols={len(targets)}")
    if not targets:
        print("nothing to append")
        return 0

    trade_dates = [
        d for d in _fetch_trade_dates(pro, anchor_last, args.end) if d > anchor_last
    ]
    print(f"fetch_days={len(trade_dates)} first={trade_dates[:1]} last={trade_dates[-1:]}")
    if not trade_dates:
        print("nothing to append")
        return 0

    unadj_anchor: dict[str, float] = {}
    adj_anchor: dict[str, float] = {}
    daily, adj = _fetch_day(pro, anchor_last, args.sleep)
    print(f"anchor {anchor_last} daily={len(daily)} adj={len(adj)}")
    if not daily.empty:
        for _, row in daily.iterrows():
            code = _code6_from_ts(row["ts_code"])
            if code not in targets:
                continue
            try:
                unadj_anchor[code] = float(row["close"])
            except (TypeError, ValueError):
                continue
    if not adj.empty:
        for _, row in adj.iterrows():
            code = _code6_from_ts(row["ts_code"])
            if code not in targets:
                continue
            try:
                adj_anchor[code] = float(row["adj_factor"])
            except (TypeError, ValueError):
                continue

    print(f"anchors unadj={len(unadj_anchor)} adj={len(adj_anchor)}")
    new_rows: dict[str, list[dict[str, str]]] = {c: [] for c in targets}
    for i, d in enumerate(trade_dates, 1):
        daily, adj = _fetch_day(pro, d, args.sleep)
        adj_map: dict[str, float] = {}
        if not adj.empty:
            for _, row in adj.iterrows():
                try:
                    adj_map[_code6_from_ts(row["ts_code"])] = float(row["adj_factor"])
                except (TypeError, ValueError, KeyError):
                    continue
        n_ok = 0
        if not daily.empty:
            for _, row in daily.iterrows():
                code = _code6_from_ts(row["ts_code"])
                last = targets.get(code)
                if last is None or d <= last:
                    continue
                u0 = unadj_anchor.get(code)
                a0 = adj_anchor.get(code)
                at = adj_map.get(code)
                tmpl = template_by_code.get(code)
                if not tmpl or not u0 or not a0 or at is None:
                    continue
                try:
                    o = float(row["open"])
                    h = float(row["high"])
                    low = float(row["low"])
                    c = float(row["close"])
                    pre = float(row.get("pre_close") or 0.0)
                    vol_hand = float(row.get("vol") or 0.0)
                    amount_k = float(row.get("amount") or 0.0)
                    local_close0 = float(tmpl["收盘价"])
                except (TypeError, ValueError, KeyError):
                    continue
                scale = (local_close0 / u0) * (at / a0)
                local_open = o * scale
                local_high = h * scale
                local_low = low * scale
                local_close = c * scale
                local_pre = pre * scale if pre else local_open
                iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
                try:
                    pct = f"{float(row.get('pct_chg') or 0.0):.3f}"
                except (TypeError, ValueError):
                    pct = tmpl.get("涨幅%", "")
                out = {k: tmpl.get(k, "") for k in fieldnames}
                out.update(
                    {
                        "日期": iso,
                        "代码": code,
                        "名称": tmpl.get("名称", ""),
                        "所属行业": tmpl.get("所属行业", ""),
                        "开盘价": f"{local_open:.3f}",
                        "最高价": f"{local_high:.3f}",
                        "最低价": f"{local_low:.3f}",
                        "收盘价": f"{local_close:.3f}",
                        "前收盘价": f"{local_pre:.3f}",
                        "成交量（股）": f"{vol_hand * 100.0:.0f}",
                        "成交额（元）": f"{amount_k * 1000.0:.2f}",
                        "涨幅%": pct,
                    }
                )
                new_rows[code].append(out)
                n_ok += 1
        print(f"[{i}/{len(trade_dates)}] {d} daily={len(daily)} appended_symbols={n_ok}")

    total_rows = sum(len(v) for v in new_rows.values())
    touched = sum(1 for v in new_rows.values() if v)
    print(f"plan_rows={total_rows} touched_files={touched} dry_run={args.dry_run}")

    # Continuity check sample
    for code in ("000001", "600519", "000408"):
        rows = new_rows.get(code) or []
        if not rows:
            continue
        print(
            "sample",
            code,
            "anchor",
            targets[code],
            template_by_code[code]["收盘价"],
            "->",
            rows[0]["日期"],
            rows[0]["收盘价"],
            "…",
            rows[-1]["日期"],
            rows[-1]["收盘价"],
        )

    if args.dry_run:
        return 0

    written = 0
    for code, rows in new_rows.items():
        if not rows:
            continue
        path = path_by_code[code]
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            for row in rows:
                writer.writerow(row)
                written += 1
    print(f"written_rows={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
