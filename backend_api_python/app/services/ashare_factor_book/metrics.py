"""Performance figures used by A-share enhanced-index reports."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def summarize(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    *,
    initial: float | None = None,
    benchmark_initial: float | None = None,
) -> dict[str, float]:
    """Absolute return plus excess, tracking error and information ratio when a benchmark exists."""
    curve = pd.to_numeric(equity, errors="coerce").dropna().sort_index()
    if curve.empty:
        return {}
    base = float(initial) if initial is not None else float(curve.iloc[0])
    if base <= 0 or len(curve) < 1:
        return {}
    total = float(curve.iloc[-1] / base - 1.0)
    elapsed = max((curve.index[-1] - curve.index[0]).days, 1) / 365.25
    annualized = float((curve.iloc[-1] / base) ** (1.0 / elapsed) - 1.0) if curve.iloc[-1] > 0 else -1.0
    wealth = np.r_[base, curve.to_numpy(dtype=float)]
    drawdown = _max_drawdown_values(wealth)
    out = {
        "total_return": total,
        "annualized_return": annualized,
        "max_drawdown": drawdown,
    }
    if benchmark is None:
        return out
    bench = pd.to_numeric(benchmark, errors="coerce").reindex(curve.index).ffill().dropna()
    aligned = curve.reindex(bench.index)
    if len(aligned) < 3 or float(bench.iloc[0]) <= 0:
        return out
    port_ret = aligned.pct_change()
    port_ret.iloc[0] = float(aligned.iloc[0] / base - 1.0)
    bench_base = float(benchmark_initial) if benchmark_initial is not None else float(bench.iloc[0])
    bench_ret = bench.pct_change()
    bench_ret.iloc[0] = float(bench.iloc[0] / bench_base - 1.0) if bench_base > 0 and benchmark_initial is not None else 0.0
    excess = (port_ret - bench_ret).dropna()
    if excess.empty:
        return out
    te = float(excess.std(ddof=0) * math.sqrt(252.0))
    ann_excess = float(excess.mean() * 252.0)
    bench_return = float(bench.iloc[-1] / bench_base - 1.0) if benchmark_initial is not None else float(bench.iloc[-1] / bench.iloc[0] - 1.0)
    out.update({
        "benchmark_return": bench_return,
        "excess_return": total - bench_return,
        "annualized_excess": ann_excess,
        "tracking_error": te,
        "information_ratio": float(ann_excess / te) if te > 1e-12 else 0.0,
    })
    return out


def year_returns(
    equity: pd.Series,
    benchmark: pd.Series | None = None,
    *,
    initial: float | None = None,
    benchmark_initial: float | None = None,
) -> list[dict[str, float | int]]:
    curve = pd.to_numeric(equity, errors="coerce").dropna().sort_index()
    rows = []
    years = sorted(set(int(stamp.year) for stamp in curve.index))
    previous = float(initial) if initial is not None else None
    bench = pd.to_numeric(benchmark, errors="coerce") if benchmark is not None else None
    previous_bench = float(benchmark_initial) if benchmark_initial is not None else None
    for year in years:
        part = curve[curve.index.year == year]
        if part.empty:
            continue
        start = float(previous) if previous is not None else float(part.iloc[0])
        end = float(part.iloc[-1])
        row: dict[str, float | int] = {"year": year, "return": end / start - 1.0 if start else 0.0}
        if bench is not None:
            bench_part = bench.reindex(part.index).ffill().dropna()
            if not bench_part.empty:
                bench_start = float(previous_bench) if previous_bench is not None else float(bench_part.iloc[0])
                bench_end = float(bench_part.iloc[-1])
                row["benchmark"] = bench_end / bench_start - 1.0 if bench_start else 0.0
                row["excess"] = float(row["return"]) - float(row["benchmark"])
                previous_bench = bench_end
        previous = end
        rows.append(row)
    return rows


def _max_drawdown_values(values: np.ndarray) -> float:
    peak = np.maximum.accumulate(values)
    drawdown = values / np.where(peak > 0, peak, 1.0) - 1.0
    value = float(np.min(drawdown)) if len(drawdown) else 0.0
    return value if np.isfinite(value) else 0.0
