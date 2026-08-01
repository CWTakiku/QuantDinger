#!/usr/bin/env python3
"""Sync CSI300 enhanced-index Tushare panels into PostgreSQL.

Usage:
    python scripts/sync_csi300_enhanced_data.py --trade-date 20260731
    python scripts/sync_csi300_enhanced_data.py --trade-date 20260731 --skip-industry
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

    from app.services.csi300_enhanced.tushare_sync import (
        fetch_and_persist_consensus_daily,
        fetch_and_persist_daily_basic,
        fetch_and_persist_flow_daily,
        fetch_and_persist_index_weights,
        fetch_and_persist_industry_map,
    )

    counts = {
        "trade_date": args.trade_date,
        "index_weights": fetch_and_persist_index_weights(trade_date=args.trade_date),
        "daily_basic": fetch_and_persist_daily_basic(trade_date=args.trade_date),
    }
    if not args.skip_industry:
        counts["industry_map"] = fetch_and_persist_industry_map()
    else:
        counts["industry_map"] = 0
    if not args.skip_flow:
        counts["flow_daily"] = fetch_and_persist_flow_daily(trade_date=args.trade_date)
    else:
        counts["flow_daily"] = 0
    if not args.skip_consensus:
        counts["consensus_daily"] = fetch_and_persist_consensus_daily(trade_date=args.trade_date)
    else:
        counts["consensus_daily"] = 0

    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
