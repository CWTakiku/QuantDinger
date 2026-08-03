#!/usr/bin/env python3
"""Sync CSI300 enhanced-index Tushare panels into PostgreSQL.

Usage:
    python scripts/sync_csi300_enhanced_data.py --trade-date 20260731
    python scripts/sync_csi300_enhanced_data.py --trade-date 20260731 --skip-industry

Celery Beat (optional day-end automation):
    task: quantdinger.tasks.csi300_enhanced_daily_sync
    schedule env: CSI300_ENHANCED_SYNC_INTERVAL_SEC (default 86400)
    enable env: ENABLE_CSI300_ENHANCED_DAILY_SYNC (default true)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync CSI300 enhanced Tushare data into DB")
    parser.add_argument(
        "--trade-date",
        default=date.today().strftime("%Y%m%d"),
        help="Trade date YYYYMMDD for index weights and daily_basic (default: today)",
    )
    parser.add_argument(
        "--skip-industry",
        action="store_true",
        help="Skip stock_basic industry map sync",
    )
    parser.add_argument(
        "--skip-flow",
        action="store_true",
        help="Skip northbound/margin flow daily sync",
    )
    parser.add_argument(
        "--skip-consensus",
        action="store_true",
        help="Skip analyst consensus daily sync",
    )
    args = parser.parse_args()

    from app.services.csi300_enhanced.tushare_sync import run_csi300_enhanced_daily_sync

    counts = run_csi300_enhanced_daily_sync(
        trade_date=args.trade_date,
        skip_industry=args.skip_industry,
        skip_flow=args.skip_flow,
        skip_consensus=args.skip_consensus,
    )
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
