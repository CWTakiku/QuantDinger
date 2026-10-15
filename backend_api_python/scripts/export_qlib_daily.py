#!/usr/bin/env python3
"""Export qd_ashare_daily_bar to per-symbol Qlib CSV (raw OHLC + adj_factor).

Usage inside the backend container:

    python scripts/export_qlib_daily.py --start 2015-01-05 --out /data/qlib_csv
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from app.services.ashare_data.qlib_export import export_range  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-05")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    def _progress(files: int, rows: int, symbol: str) -> None:
        print(f"files={files} rows={rows} last={symbol}", flush=True)

    result = export_range(args.out, args.start, args.end, on_progress=_progress)
    print(f"done files={result['files']} rows={result['rows']}", flush=True)


if __name__ == "__main__":
    main()
