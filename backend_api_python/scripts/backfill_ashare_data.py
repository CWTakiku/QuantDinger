#!/usr/bin/env python3
"""Backfill the A-share data platform (``qd_ashare_*``) from Tushare.

Usage (inside the backend container):

    python scripts/backfill_ashare_data.py --start 20150101
    python scripts/backfill_ashare_data.py --start 20150101 --end 20151231 --no-rebuild-adj
    python scripts/backfill_ashare_data.py --rebuild-adj-only

Resumable: trade dates already logged ``ok`` are skipped unless ``--no-resume``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from app.services.ashare_data import backfill, rebuild_adj_factors  # noqa: E402


def _date(text: str):
    return datetime.strptime(text.replace("-", ""), "%Y%m%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20150101")
    parser.add_argument("--end", default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"))
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--no-reference", action="store_true", help="skip stock_basic / namechange refresh")
    parser.add_argument("--no-rebuild-adj", action="store_true")
    parser.add_argument("--rebuild-adj-only", action="store_true")
    parser.add_argument(
        "--local-pack",
        help="extracted Tushare per-API archive; APIs missing from it are pulled from Tushare",
    )
    parser.add_argument("--compare-adj", action="store_true", help="check derived adj_factor against the pack")
    args = parser.parse_args()

    if args.rebuild_adj_only:
        print(json.dumps({"adj_updated": rebuild_adj_factors()}))
        return 0

    def progress(item: dict) -> None:
        print(
            f"{item['trade_date']} {item['status']:<7} bars={item['bars']:<5} "
            f"remaining={item.get('remaining', ''):<5} {item['detail'] if item['status'] != 'ok' else ''}",
            flush=True,
        )

    if args.local_pack:
        from app.services.ashare_data.local_pack import compare_adj_factors, import_local_pack

        pack = Path(args.local_pack)
        if args.compare_adj:
            print(json.dumps(compare_adj_factors(pack, _date(args.start), _date(args.end)), ensure_ascii=False))
            return 0
        summary = import_local_pack(
            pack,
            _date(args.start),
            _date(args.end),
            resume=not args.no_resume,
            rebuild_adjustment=not args.no_rebuild_adj,
            progress=progress,
        )
        print(json.dumps(summary, ensure_ascii=False, default=str))
        return 0 if not summary["failed"] else 1

    summary = backfill(
        _date(args.start),
        _date(args.end),
        resume=not args.no_resume,
        refresh_reference=not args.no_reference,
        rebuild_adjustment=not args.no_rebuild_adj,
        progress=progress,
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
