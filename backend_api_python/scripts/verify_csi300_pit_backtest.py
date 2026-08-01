#!/usr/bin/env python3
"""Short CSI300 Enhanced v2 backtest to verify no membership look-ahead."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.csi300_enhanced.bench import get_csi300_bench_weights_with_meta
from app.services.strategy_v2.service import StrategyV2BacktestService
from app.services.universe import UniverseService
from app.utils.db import get_db_connection


def main() -> int:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_users ORDER BY id LIMIT 1")
        user_id = int(cur.fetchone()["id"])
        cur.execute(
            "SELECT id FROM qd_universes WHERE code = %s",
            ("csi300",),
        )
        universe_id = int(cur.fetchone()["id"])
        cur.execute(
            "SELECT code FROM qd_script_templates WHERE template_key = %s",
            ("strategy_v2_csi300_enhanced_v2",),
        )
        code = cur.fetchone()["code"]

    uni = UniverseService()
    checks = {}
    for day in (datetime(2021, 8, 2).date(), datetime(2021, 8, 31).date(), datetime(2022, 1, 31).date()):
        members = uni.resolve_members(user_id, universe_id, as_of=day)
        codes = {m["symbol"] for m in members}
        checks[str(day)] = {
            "n": len(members),
            "has_300308": any(c.startswith("300308") for c in codes),
        }

    w_20210831, src_20210831 = get_csi300_bench_weights_with_meta(datetime(2021, 8, 31).date())
    w_20260630, src_20260630 = get_csi300_bench_weights_with_meta(datetime(2026, 6, 30).date())

    print("PIT_MEMBERSHIP", json.dumps(checks, ensure_ascii=False))
    print(
        "BENCH",
        json.dumps(
            {
                "2021-08-31": {
                    "source": src_20210831,
                    "n": len(w_20210831),
                    "has_300308": "CNStock:300308.SZ" in w_20210831,
                },
                "2026-06-30": {
                    "source": src_20260630,
                    "n": len(w_20260630),
                    "w_300308": round(float(w_20260630.get("CNStock:300308.SZ") or 0.0), 6),
                },
            },
            ensure_ascii=False,
        ),
    )

    print("START_BACKTEST", flush=True)
    svc = StrategyV2BacktestService()
    run_id, result = svc.run(
        user_id=user_id,
        code=code,
        start_date=datetime(2021, 8, 31),
        end_date=datetime(2021, 12, 31, 23, 59, 59),
        initial_capital=1_000_000,
        leverage_enabled=False,
        commission=0.0005,
        slippage=0.0005,
        params={
            "universe_top_n": 50,
            "use_icir": False,
            "regime_enabled": True,
        },
        persist=False,
    )

    symbols: set[str] = set()
    for key in ("trades", "orders", "positions", "finalPositions", "holdings"):
        rows = result.get(key) or []
        if isinstance(rows, dict):
            symbols.update(str(k) for k in rows)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in ("symbol", "instrument", "key", "code"):
                if row.get(field):
                    symbols.add(str(row[field]))

    blob = json.dumps(result, default=str)
    metrics = result.get("metrics") or result.get("summary") or {}
    summary = {
        "run_id": run_id,
        "result_keys": sorted(result.keys()),
        "n_trades": len(result.get("trades") or []) if isinstance(result.get("trades"), list) else None,
        "n_symbols_seen": len(symbols),
        "has_300308_anywhere": "300308" in blob,
        "has_300308_in_symbols": any("300308" in s for s in symbols),
        "symbols_sample": sorted(symbols)[:15],
        "metrics": {
            k: metrics.get(k)
            for k in ("totalReturn", "annualReturn", "sharpe", "maxDrawdown", "turnover")
            if isinstance(metrics, dict) and k in metrics
        },
    }
    print("BACKTEST_SUMMARY", json.dumps(summary, ensure_ascii=False, default=str))
    ok = (
        not checks["2021-08-02"]["has_300308"]
        and not checks["2021-08-31"]["has_300308"]
        and not summary["has_300308_anywhere"]
    )
    print("VERIFY_OK" if ok else "VERIFY_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
