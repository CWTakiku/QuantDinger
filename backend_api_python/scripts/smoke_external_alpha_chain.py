#!/usr/bin/env python3
"""End-to-end smoke: synthetic scores → persist → Strategy V2 weekly backtest.

Mimics the research-side CSV handoff without installing QuantaAlpha.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _example_path() -> Path:
    candidates = [
        REPO / "docs" / "examples" / "strategy_v2_external_alpha_score_weekly.py",
        ROOT / "docs" / "examples" / "strategy_v2_external_alpha_score_weekly.py",
        Path("/tmp/strategy_v2_external_alpha_score_weekly.py"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("strategy_v2_external_alpha_score_weekly.py not found")


def _friday_asofs(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() == 4:  # Friday — score before next Monday rebalance
            days.append(cur)
        cur += timedelta(days=1)
    return days or [start]


def _pick_symbols(limit: int, as_of: date) -> list[str]:
    from app.utils.db import get_db_connection
    from app.markets.cn_stock.symbols import canonicalize_cnstock_key

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT m.symbol
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = 'csi300'
              AND m.valid_from <= ?
              AND (m.valid_to IS NULL OR m.valid_to > ?)
            ORDER BY COALESCE(m.member_weight, 0) DESC, m.symbol
            LIMIT ?
            """,
            (as_of, as_of, limit),
        )
        rows = list(cur.fetchall() or [])
    out: list[str] = []
    for row in rows:
        key = canonicalize_cnstock_key(str(row["symbol"]))
        if key and key not in out:
            out.append(key)
    return out


def _write_csv(path: Path, symbols: list[str], asofs: list[date], *, source: str, version: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["as_of", "symbol", "score", "source", "version", "universe"])
        for d in asofs:
            for i, sym in enumerate(symbols):
                # Stable pseudo-alpha: higher weight ranks get higher base score + day drift
                score = round(3.0 - i * 0.03 + (d.toordinal() % 7) * 0.01, 6)
                bare = sym.split(":", 1)[-1]
                w.writerow([d.isoformat(), bare, score, source, version, "csi300"])
                n += 1
    return n


def _ensure_template() -> None:
    from app.utils.db import get_db_connection

    code = _example_path().read_text(encoding="utf-8")
    schema = {
        "params": [
            {"name": "source", "type": "text", "default": "external", "labelKey": "strategyV2.params.alphaSource"},
            {"name": "version", "type": "text", "default": "default", "labelKey": "strategyV2.params.alphaVersion"},
            {"name": "top_n", "type": "integer", "default": 30, "min": 5, "max": 100, "step": 1, "labelKey": "strategyV2.params.topN"},
            {"name": "min_names", "type": "integer", "default": 10, "min": 3, "max": 50, "step": 1, "labelKey": "strategyV2.params.minNames"},
            {"name": "score_lag_days", "type": "integer", "default": 1, "min": 0, "max": 5, "step": 1, "labelKey": "strategyV2.params.scoreLagDays"},
        ]
    }
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_script_templates
            (template_key, asset_type, title, description, code, param_schema, tags, icon, accent, sort_order, is_active, metadata, updated_at)
            VALUES (?, 'portfolio_strategy', ?, ?, ?, ?::jsonb, ?::jsonb, 'fund', 'purple', 102, TRUE, ?::jsonb, NOW())
            ON CONFLICT (template_key) DO UPDATE SET
              code = EXCLUDED.code,
              param_schema = EXCLUDED.param_schema,
              is_active = TRUE,
              updated_at = NOW()
            """,
            (
                "strategy_v2_external_alpha_score",
                "External Alpha Score Weekly",
                "Weekly CSI300 Top-N from imported external alpha scores.",
                code,
                json.dumps(schema, ensure_ascii=False),
                json.dumps(["strategy-v2", "portfolio", "csi300", "external-alpha"], ensure_ascii=False),
                json.dumps({"source": "smoke_chain", "apiVersion": 2}, ensure_ascii=False),
            ),
        )
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="smoke_chain")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--symbols", type=int, default=40)
    parser.add_argument("--start", default="2026-06-16")
    parser.add_argument("--end", default="2026-07-31")
    parser.add_argument("--csv", default="/tmp/external_alpha_smoke.csv")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--min-names", type=int, default=5)
    parser.add_argument("--universe-top-n", type=int, default=40)
    parser.add_argument("--skip-backtest", action="store_true")
    args = parser.parse_args()

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    asofs = _friday_asofs(start_d - timedelta(days=7), end_d)
    symbols = _pick_symbols(args.symbols, as_of=end_d)
    if len(symbols) < args.min_names:
        print(json.dumps({"ok": False, "error": "not_enough_universe_symbols", "n": len(symbols)}, ensure_ascii=False))
        return 2

    csv_path = Path(args.csv)
    n_rows = _write_csv(csv_path, symbols, asofs, source=args.source, version=args.version)

    from app.services.external_alpha.store import (
        load_external_alpha_scores_as_of,
        persist_external_alpha_scores,
        rows_from_csv_text,
    )

    rows = rows_from_csv_text(csv_path.read_text(encoding="utf-8"))
    import_result = persist_external_alpha_scores(rows)
    pit = load_external_alpha_scores_as_of(end_d, source=args.source, version=args.version)
    _ensure_template()

    summary: dict = {
        "ok": True,
        "csv": str(csv_path),
        "csv_rows": n_rows,
        "symbols": len(symbols),
        "asofs": [d.isoformat() for d in asofs],
        "import": import_result,
        "pit_load_count": int(pit.shape[0]),
        "sample_symbols": symbols[:5],
    }

    if args.skip_backtest:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if import_result.get("inserted", 0) > 0 else 1

    code = _example_path().read_text(encoding="utf-8")
    from app.services.strategy_v2.service import StrategyV2BacktestService

    svc = StrategyV2BacktestService()
    run_id, result = svc.run(
        user_id=args.user_id,
        code=code,
        start_date=datetime.combine(start_d, datetime.min.time()),
        end_date=datetime.combine(end_d, datetime.max.time().replace(microsecond=0)),
        initial_capital=1_000_000.0,
        commission=0.0003,
        slippage=0.0005,
        params={
            "source": args.source,
            "version": args.version,
            "top_n": args.top_n,
            "min_names": args.min_names,
            "score_lag_days": 1,
            "universe_top_n": args.universe_top_n,
        },
        persist=False,
        strategy_name="smoke_external_alpha_chain",
    )
    logs = list(result.get("logs") or [])
    rebalance_logs = [x for x in logs if "ext_alpha rebalance" in str(x)]
    skip_logs = [x for x in logs if "skip rebalance" in str(x)]
    summary["backtest"] = {
        "runId": run_id,
        "totalExecutions": result.get("totalExecutions"),
        "finalEquity": result.get("finalEquity") or result.get("final_equity"),
        "totalReturn": result.get("totalReturn") or result.get("total_return"),
        "rebalanceLogCount": len(rebalance_logs),
        "skipLogCount": len(skip_logs),
        "sampleLogs": (rebalance_logs or skip_logs or logs)[:8],
    }
    ok = bool(result.get("totalExecutions")) and len(rebalance_logs) > 0
    summary["ok"] = ok
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
