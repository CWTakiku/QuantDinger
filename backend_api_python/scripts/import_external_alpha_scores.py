#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.external_alpha.store import persist_external_alpha_scores, rows_from_csv_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--source", default="external")
    p.add_argument("--version", default="default")
    p.add_argument("--universe", default="")
    args = p.parse_args()
    text = Path(args.csv).read_text(encoding="utf-8")
    rows = rows_from_csv_text(
        text,
        default_source=args.source,
        default_version=args.version,
        default_universe=args.universe,
    )
    result = persist_external_alpha_scores(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("inserted", 0) > 0 or result.get("skipped", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
