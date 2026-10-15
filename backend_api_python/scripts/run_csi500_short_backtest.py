#!/usr/bin/env python3
"""Run the CSI500 short strategy and print the summary. Does not save a run."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from app.services.strategy_v2.service import StrategyV2BacktestService  # noqa: E402


def _shanghai_day(stamp: str) -> str:
    text = str(stamp or "")
    if len(text) < 10:
        return ""
    try:
        import pandas as pd

        moment = pd.Timestamp(text)
        if moment.tzinfo is None:
            moment = moment.tz_localize("UTC")
        return moment.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
    except Exception:
        return text[:10]


def _year_returns(curve: list[dict]) -> list[tuple[str, float]]:
    by_year: dict[str, list[float]] = {}
    for point in curve:
        day = _shanghai_day(str(point.get("time") or ""))
        value = float(point.get("value") or 0.0)
        if len(day) < 4 or value <= 0:
            continue
        by_year.setdefault(day[:4], []).append(value)
    out = []
    previous = None
    for year in sorted(by_year):
        end = by_year[year][-1]
        start = previous if previous is not None else by_year[year][0]
        out.append((year, (end / start - 1.0) * 100.0 if start else 0.0))
        previous = end
    return out


def _board(symbol: str) -> str:
    code = str(symbol).split(":")[-1].upper().split(".")[0]
    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith(("60", "00")):
        return "主板"
    return "其他"


def _board_pnl(trades: list[dict]) -> list[str]:
    buckets: dict[str, list[float]] = {}
    reasons: dict[str, list[float]] = {}
    for trade in trades:
        profit = float(trade.get("profit") or 0.0)
        buckets.setdefault(_board(str(trade.get("symbol") or "")), []).append(profit)
        reasons.setdefault(str(trade.get("close_reason") or "unknown"), []).append(profit)
    lines = []
    for name in ("主板", "创业板", "科创板", "其他"):
        values = buckets.get(name) or []
        if not values:
            continue
        wins = sum(1 for item in values if item > 0)
        lines.append(
            f"board {name} trades={len(values)} pnl={sum(values):.0f} win={wins / len(values) * 100:.1f}%"
        )
    for name in sorted(reasons):
        values = reasons[name]
        lines.append(f"exit {name} trades={len(values)} pnl={sum(values):.0f}")
    return lines


def _halt_day(curve: list[dict], limit: float = -15.0) -> str:
    for point in curve:
        if float(point.get("drawdown") or 0.0) <= limit:
            return _shanghai_day(str(point.get("time") or ""))
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=500_000.0)
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args()
    code = Path(args.code).read_text(encoding="utf-8")
    _, result = StrategyV2BacktestService().run(
        user_id=args.user_id,
        code=code,
        start_date=datetime.fromisoformat(args.start),
        end_date=datetime.fromisoformat(args.end),
        initial_capital=args.capital,
        commission=0.00025,
        slippage=0.002,
        persist=False,
        analysis_only=True,
    )
    curve = result.get("equityCurve") or []
    skipped = result.get("symbolsSkipped") or []
    print(f"window={args.start}..{args.end}")
    print(f"return={float(result.get('totalReturn') or 0):.2f}%")
    if "benchmarkTotalReturn" in result:
        print(f"benchmark={float(result.get('benchmarkTotalReturn') or 0):.2f}%")
        print(f"excess={float(result.get('excessReturn') or 0):.2f}%")
    print(f"max_drawdown={float(result.get('maxDrawdown') or 0):.2f}%")
    print(f"sharpe={float(result.get('sharpeRatio') or 0):.2f}")
    print(f"annualized={float(result.get('annualizedReturn') or 0):.2f}%")
    print(f"final={float(result.get('finalEquity') or 0):.2f}")
    print(f"trades={result.get('totalTrades')} executions={result.get('totalExecutions')}")
    print(f"win_rate={result.get('winRate')} profit_factor={result.get('profitFactor')}")
    print(f"symbols={result.get('symbolsUsed')} skipped={len(skipped)} status={result.get('resultStatus')}")
    halt = _halt_day(curve)
    if halt:
        print(f"drawdown_15_on={halt}")
    for year, change in _year_returns(curve):
        print(f"year {year} {change:.2f}%")
    for line in _board_pnl(result.get("trades") or []):
        print(line)


if __name__ == "__main__":
    main()
