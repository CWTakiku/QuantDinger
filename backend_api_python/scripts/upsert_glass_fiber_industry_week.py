#!/usr/bin/env python3
"""Manual upsert for glass fiber industry weekly signals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.industry_glass_fiber.store import upsert_glass_fiber_week


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upsert a glass fiber industry weekly signal row.",
    )
    parser.add_argument("--as-of", required=True, help="Week as-of date (YYYY-MM-DD)")
    parser.add_argument("--cloth-trend", type=int, required=True, choices=[-1, 0, 1])
    parser.add_argument("--inventory-trend", type=int, required=True, choices=[-1, 0, 1])
    parser.add_argument("--source", default="manual")
    parser.add_argument("--new-capacity-flag", type=int, default=0, choices=[0, 1])
    parser.add_argument("--confidence", type=float, default=1.0)
    parser.add_argument("--cloth-7628-mid", type=float, default=None)
    parser.add_argument("--yarn-2400-mid", type=float, default=None)
    parser.add_argument(
        "--raw-ref",
        action="append",
        default=[],
        help="Optional raw reference URL (repeatable)",
    )
    args = parser.parse_args()

    if not 0.0 <= args.confidence <= 1.0:
        print("confidence must be between 0 and 1", file=sys.stderr)
        return 2

    row = {
        "as_of": args.as_of,
        "cloth_trend": args.cloth_trend,
        "inventory_trend": args.inventory_trend,
        "new_capacity_flag": args.new_capacity_flag,
        "source": args.source,
        "confidence": args.confidence,
        "raw_refs": {"urls": list(args.raw_ref)} if args.raw_ref else {},
    }
    if args.cloth_7628_mid is not None:
        row["cloth_7628_mid"] = args.cloth_7628_mid
    if args.yarn_2400_mid is not None:
        row["yarn_2400_mid"] = args.yarn_2400_mid

    saved = upsert_glass_fiber_week(row)
    print(json.dumps(saved, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
