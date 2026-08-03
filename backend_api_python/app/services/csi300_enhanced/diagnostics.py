"""Enhanced-index rebalance diagnostics for backtest result enrichment."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def build_enhanced_index_diagnostics(
    *,
    as_of: str,
    weights: Mapping[str, float],
    w_bench: Mapping[str, float],
    optimize_result: Mapping[str, Any],
    bench_source: str,
    industry: Mapping[str, str] | None = None,
    size_z: Mapping[str, float] | pd.Series | None = None,
    kind: str = "weekly",
) -> dict[str, Any]:
    """Build one rebalance diagnostic record from optimizer output and exposures."""
    w = _weight_series(weights)
    wb = _weight_series(w_bench)
    support = w.index.union(wb.index)
    w = w.reindex(support).fillna(0.0)
    wb = wb.reindex(support).fillna(0.0)
    if float(wb.sum()) > 0:
        wb = wb / float(wb.sum())

    active = w - wb
    te_exante = float(optimize_result.get("active_risk_proxy") or 0.0)
    size_exposure = _size_exposure(active.to_numpy(dtype=float), wb.to_numpy(dtype=float), size_z, support)
    industry_dev = _industry_active_deviation(w, wb, industry)

    source = str(bench_source or "equal_fallback")
    bench_fallback = source != "csi300_pit"

    return {
        "asOf": str(as_of),
        "kind": str(kind or "weekly"),
        "benchSource": source,
        "benchFallback": bench_fallback,
        "teExante": te_exante,
        "activeRiskProxy": te_exante,
        "sizeExposure": size_exposure,
        "optimizerStatus": str(optimize_result.get("status") or ""),
        "turnover": float(optimize_result.get("turnover") or 0.0),
        "industryActiveDeviation": industry_dev,
    }


def summarize_enhanced_index_diagnostics(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate rebalance diagnostics for backtest result output."""
    if not records:
        return {}

    te_values = [float(item.get("teExante") or item.get("activeRiskProxy") or 0.0) for item in records]
    size_values = [float(item.get("sizeExposure") or 0.0) for item in records]
    max_industry = [
        float((item.get("industryActiveDeviation") or {}).get("maxAbs") or 0.0)
        for item in records
    ]
    fallback_count = sum(1 for item in records if bool(item.get("benchFallback")))

    return {
        "rebalanceCount": len(records),
        "last": dict(records[-1]),
        "avgTeExante": float(np.mean(te_values)) if te_values else 0.0,
        "maxTeExante": float(np.max(te_values)) if te_values else 0.0,
        "avgSizeExposure": float(np.mean(size_values)) if size_values else 0.0,
        "maxIndustryActiveDeviation": float(np.max(max_industry)) if max_industry else 0.0,
        "benchFallbackRate": float(fallback_count) / float(len(records)),
        "rebalances": [dict(item) for item in records],
    }


def _weight_series(values: Mapping[str, float] | pd.Series | None) -> pd.Series:
    if values is None:
        return pd.Series(dtype=float)
    if isinstance(values, pd.Series):
        series = pd.to_numeric(values, errors="coerce")
        return series.replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    if not values:
        return pd.Series(dtype=float)
    clean: dict[str, float] = {}
    for key, value in dict(values).items():
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(parsed):
            clean[str(key)] = parsed
    return pd.Series(clean, dtype=float)


def _size_exposure(
    active: np.ndarray,
    wb: np.ndarray,
    size_z: Mapping[str, float] | pd.Series | None,
    support: pd.Index,
) -> float:
    if size_z is None:
        return 0.0
    sz = _weight_series(size_z).reindex(support).fillna(0.0).to_numpy(dtype=float)
    return float(np.dot(active, sz))


def _industry_active_deviation(
    weights: pd.Series,
    wb: pd.Series,
    industry: Mapping[str, str] | None,
) -> dict[str, Any]:
    if not industry:
        return {"maxAbs": 0.0, "byIndustry": {}}

    ind = pd.Series({str(k): str(v) for k, v in dict(industry).items()})
    by_industry: dict[str, float] = {}
    for code in sorted(set(ind.reindex(weights.index).fillna("UNKNOWN"))):
        mask = ind.reindex(weights.index).fillna("UNKNOWN") == code
        if not bool(mask.any()):
            continue
        by_industry[code] = float(weights[mask].sum() - wb[mask].sum())

    max_abs = max((abs(v) for v in by_industry.values()), default=0.0)
    return {"maxAbs": float(max_abs), "byIndustry": by_industry}
