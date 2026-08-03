#!/usr/bin/env python3
"""Rebuild public csi300 universe members from PIT index_weight history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.csi300_enhanced.tushare_sync import (  # noqa: E402
    rebuild_csi300_membership_from_index_weights,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-code", default="csi300")
    args = parser.parse_args()
    result = rebuild_csi300_membership_from_index_weights(universe_code=args.universe_code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if int(result.get("members_inserted") or 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
