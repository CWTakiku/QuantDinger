#!/usr/bin/env python3
"""Convert Qlib / RD-Agent prediction outputs to QuantDinger external-alpha CSV.

Accepts common layouts:
- MultiIndex (datetime, instrument) Series/DataFrame with score column
- Columns: datetime|date|as_of + instrument|symbol + score|pred|label

Example:
  python scripts/export_qlib_pred_to_qd_csv.py \\
    --input logs/.../pred.pkl \\
    --output exports/rdagent_scores.csv \\
    --source rdagent --version v1 --universe csi300
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _load(path: Path) -> pd.DataFrame | pd.Series:
    suf = path.suffix.lower()
    if suf == ".parquet":
        return pd.read_parquet(path)
    if suf in {".pkl", ".pickle"}:
        return pd.read_pickle(path)
    if suf == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported input type: {suf}")


def _normalize(obj: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(obj, pd.Series):
        frame = obj.rename("score").to_frame()
    else:
        frame = obj.copy()

    if isinstance(frame.index, pd.MultiIndex) and frame.index.nlevels >= 2:
        frame = frame.reset_index()

    colmap = {str(c).lower(): c for c in frame.columns}
    date_col = next((colmap[k] for k in ("datetime", "date", "as_of", "trade_date") if k in colmap), None)
    sym_col = next((colmap[k] for k in ("instrument", "symbol", "code", "ticker") if k in colmap), None)
    score_col = next((colmap[k] for k in ("score", "pred", "prediction", "label", "y_pred") if k in colmap), None)
    if score_col is None and len(frame.columns) == 1:
        score_col = frame.columns[0]
    if date_col is None or sym_col is None or score_col is None:
        raise ValueError(
            f"cannot infer columns from {list(frame.columns)}; need date/symbol/score"
        )

    out = pd.DataFrame(
        {
            "as_of": pd.to_datetime(frame[date_col]).dt.strftime("%Y-%m-%d"),
            "symbol": frame[sym_col].astype(str).str.strip(),
            "score": pd.to_numeric(frame[score_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["as_of", "symbol", "score"])
    # Qlib often uses SH600519 / SZ000001
    def _to_qd(sym: str) -> str:
        s = sym.upper().replace("CNSTOCK:", "")
        if s.startswith("SH") and len(s) == 8 and s[2:].isdigit():
            return f"{s[2:]}.SH"
        if s.startswith("SZ") and len(s) == 8 and s[2:].isdigit():
            return f"{s[2:]}.SZ"
        if s.endswith(".SH") or s.endswith(".SZ"):
            return s
        if s.isdigit() and len(s) == 6:
            return s
        return s

    out["symbol"] = out["symbol"].map(_to_qd)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", default="rdagent")
    parser.add_argument("--version", default="default")
    parser.add_argument("--universe", default="csi300")
    args = parser.parse_args()

    frame = _normalize(_load(args.input))
    frame["source"] = args.source
    frame["version"] = args.version
    frame["universe"] = args.universe
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame[["as_of", "symbol", "score", "source", "version", "universe"]].to_csv(
        args.output, index=False
    )
    print(
        {
            "rows": int(len(frame)),
            "as_of_min": frame["as_of"].min(),
            "as_of_max": frame["as_of"].max(),
            "output": str(args.output),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
