#!/usr/bin/env python3
"""Walk-forward CSI500 factor book. --persist writes one page history row."""

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

from app.services.ashare_factor_book.load import load_research_inputs  # noqa: E402
from app.services.ashare_factor_book.pipeline import BUFFER, MAX_WEIGHT, run_book  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-10-09")
    parser.add_argument("--capital", type=float, default=500_000.0)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    frames, members, benchmark, benchmark_name = load_research_inputs(start, end)
    print(f"members={len(members)} symbols={frames['adj_close'].shape[1]} days={frames['adj_close'].shape[0]}", flush=True)
    print(f"benchmark={benchmark_name or 'missing'} points={len(benchmark)}", flush=True)
    result = run_book(
        frames,
        members,
        benchmark if len(benchmark) else None,
        start=start,
        end=end,
        capital=args.capital,
    )
    _print(result, benchmark_name)
    if args.persist:
        run_id = _persist(result, benchmark_name, start, end, symbols=int(frames["adj_close"].shape[1]))
        print(f"persisted factor research id={run_id}", flush=True)


def _print(result: dict, benchmark_name: str) -> None:
    summary = result["summary"]
    print(f"holdings_target={result['holdings_target']} buffer={BUFFER} max_weight={MAX_WEIGHT:.0%}")
    print(f"slippage={result['slippage']:.2%} capital={result['capital']:.0f}")
    print(f"return={summary.get('total_return', 0.0) * 100:.2f}%")
    print(f"annualized={summary.get('annualized_return', 0.0) * 100:.2f}%")
    print(f"max_drawdown={summary.get('max_drawdown', 0.0) * 100:.2f}%")
    if "benchmark_return" in summary:
        print(f"benchmark={benchmark_name} {summary['benchmark_return'] * 100:.2f}%")
        print(f"excess={summary.get('excess_return', 0.0) * 100:.2f}%")
        print(f"tracking_error={summary.get('tracking_error', 0.0) * 100:.2f}%")
        print(f"information_ratio={summary.get('information_ratio', 0.0):.2f}")
    held = result["holdings"]
    turned = result["turnover"]
    if len(held):
        print(f"avg_names={float(held.mean()):.1f}")
    if len(turned):
        print(f"avg_turnover={float(turned.mean()) * 100:.2f}% fills={result['fills']} fees={result['fees']:.0f}")
    chart = result.get("chart") or {}
    if chart.get("icSeries"):
        print(
            f"score_rank_ic={float(chart.get('rankIc') or 0.0):.4f} "
            f"score_icir={float(chart.get('icir') or 0.0):.3f} "
            f"groups={len(chart.get('groupCurves') or [])} "
            f"ic_points={len(chart.get('icSeries') or [])}"
        )
    for row in result["years"]:
        text = f"year {int(row['year'])} {float(row['return']) * 100:.2f}%"
        if "benchmark" in row:
            text += f" bench {float(row['benchmark']) * 100:.2f}% excess {float(row['excess']) * 100:.2f}%"
        print(text)
    for quarter in result["quarters"]:
        parts = []
        for name, item in quarter["weights"].items():
            if item["weight"] <= 0:
                continue
            sign = float(item["sign"])
            live = float(quarter["realized_ic"].get(name, 0.0))
            parts.append(
                f"{name} w={item['weight']:.2f} "
                f"train_ic={sign * float(item['mean_ic']):.3f} live_ic={sign * live:.3f}"
            )
        print(f"quarter {quarter['start']} train_end={quarter['train_end']} " + (" ".join(parts) or "no-factor"))


def _curve(series, base: float) -> list[dict]:
    import pandas as pd

    points = []
    origin = float(base) if base else None
    for stamp, value in series.items():
        number = float(value)
        if origin is None and number:
            origin = number
        if not origin:
            continue
        points.append({"time": pd.Timestamp(stamp).strftime("%Y-%m-%d"), "value": round(number / origin, 6)})
    return points


def _persist(result: dict, benchmark_name: str, start: date, end: date, symbols: int) -> int | None:
    from app.services.strategy_v2.storage import FactorResearchRepository
    from app.utils.db import get_db_connection

    summary = result["summary"]
    held = result["holdings"]
    turned = result["turnover"]
    equity = result["equity"]
    benchmark = result["benchmark"]
    chart = result.get("chart") or {}
    payload = {
        "factorId": "csi500_enhancement",
        "rankIc": chart.get("rankIc"),
        "icir": chart.get("icir"),
        "icPositiveRate": chart.get("icPositiveRate"),
        "coverage": chart.get("coverage"),
        "missingRate": chart.get("missingRate"),
        "monotonicity": chart.get("monotonicity"),
        "averageTurnover": chart.get("averageTurnover"),
        "grossLongShortReturn": chart.get("grossLongShortReturn"),
        "netLongShortReturn": chart.get("netLongShortReturn"),
        "symbolsUsed": int(symbols),
        "icSeries": chart.get("icSeries") or [],
        "groupCurves": chart.get("groupCurves") or [],
        "longShortCurve": chart.get("longShortCurve") or [],
        "groupObservations": chart.get("groupObservations") or [],
        "stability": chart.get("stability") or {},
        "factorCorrelation": chart.get("factorCorrelation") or {},
        "enhancement": {
            "name": "中证500抽样增强",
            "benchmarkName": benchmark_name or "中证500全收益",
            "note": "40 只抽样组合，相对中证 500 全收益。超额为负是这次检验的结果，没有在同一段样本上再调参数。",
            "totalReturn": float(summary.get("total_return") or 0.0),
            "benchmarkReturn": float(summary.get("benchmark_return") or 0.0),
            "excessReturn": float(summary.get("excess_return") or 0.0),
            "informationRatio": float(summary.get("information_ratio") or 0.0),
            "trackingError": float(summary.get("tracking_error") or 0.0),
            "maxDrawdown": float(summary.get("max_drawdown") or 0.0),
            "averageHoldings": float(held.mean()) if len(held) else 0.0,
            "averageTurnover": float(turned.mean()) if len(turned) else 0.0,
            "fees": float(result.get("fees") or 0.0),
            "years": [
                {
                    "year": int(row["year"]),
                    "portfolio": float(row["return"]),
                    "benchmark": float(row.get("benchmark") or 0.0),
                    "excess": float(row.get("excess") or 0.0),
                }
                for row in result["years"]
            ],
            "equity": _curve(equity, float(result["capital"])),
            "benchmark": _curve(benchmark, float(benchmark.iloc[0]) if len(benchmark) else 1.0),
        },
    }
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "DELETE FROM qd_factor_research_runs WHERE user_id = ? AND factor_id = ?",
            (1, "csi500_enhancement"),
        )
        db.commit()
        cur.close()
    run_id = FactorResearchRepository().persist_run(
        user_id=1,
        source_id=0,
        source_name="中证500抽样增强",
        market="CNStock",
        timeframe="1d",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        factor_id="csi500_enhancement",
        groups=40,
        holding_period=5,
        commission=0.00025,
        slippage=float(result["slippage"]),
        neutralize_industry=False,
        manifest={"name": "中证500抽样增强", "universe": "csi500"},
        result=payload,
        code="",
    )
    if run_id:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_factor_research_runs SET icir = ?, net_long_short_return = ? WHERE id = ?",
                (
                    float(summary.get("information_ratio") or 0.0),
                    float(summary.get("excess_return") or 0.0),
                    int(run_id),
                ),
            )
            db.commit()
            cur.close()
    return run_id


if __name__ == "__main__":
    main()
