"""Diagonal-risk enhanced-index optimizer (numpy-only closed form + projection)."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def optimize_enhanced_index(
    alpha: Mapping[str, float] | pd.Series,
    w_bench: Mapping[str, float] | pd.Series,
    *,
    w_prev: Mapping[str, float] | pd.Series | None = None,
    active_limit: float = 0.025,
    industry: Mapping[str, str] | pd.Series | None = None,
    industry_limit: float = 0.05,
    idio_var: Mapping[str, float] | pd.Series | None = None,
    risk_aversion: float = 1.0,
    turn_penalty: float = 0.01,
    max_iter: int = 80,
) -> dict[str, Any]:
    """
    Solve approximate mean-variance active portfolio with box + simplex (+ industry).

    Unconstrained active weight for diagonal D:
        active_i = alpha_i / (2 * λ * d_i)
    then blend toward previous weights by turn_penalty and project.
    """
    a = _series(alpha)
    if a.empty:
        return {"weights": {}, "status": "empty", "turnover": 0.0, "active_risk_proxy": 0.0}

    support = a.index
    wb = _series(w_bench).reindex(support).fillna(0.0)
    if float(wb.sum()) > 0:
        wb = wb / float(wb.sum())
    else:
        wb = pd.Series(1.0 / len(support), index=support)

    prev = _series(w_prev if w_prev is not None else wb).reindex(support).fillna(0.0)
    d = _series(idio_var if idio_var is not None else {k: 1.0 for k in support}).reindex(support).fillna(1.0)
    d = d.clip(lower=1e-6)

    lam = max(float(risk_aversion), 1e-8)
    # Active portfolio requires zero-sum alpha in practice; demean for stability.
    a = a - float(a.mean())
    active = a / (2.0 * lam * d)
    # Soft turnover blend: shrink active toward (prev - wb)
    gam = max(float(turn_penalty), 0.0)
    if gam > 0:
        shrink = 1.0 / (1.0 + gam)
        active = shrink * active + (1.0 - shrink) * (prev - wb)

    lo = (wb - float(active_limit)).clip(lower=0.0)
    hi = (wb + float(active_limit)).clip(upper=1.0)
    w = (wb + active).clip(lower=lo, upper=hi).to_numpy(dtype=float)
    lo_v = lo.to_numpy(dtype=float)
    hi_v = hi.to_numpy(dtype=float)
    wb_v = wb.to_numpy(dtype=float)

    ind_codes = None
    if industry is not None:
        ind_series = pd.Series({str(k): str(v) for k, v in dict(industry).items()})
        ind_codes = ind_series.reindex(support).fillna("UNKNOWN").to_numpy()

    for _ in range(int(max_iter)):
        w = _project_simplex_box(w, lo_v, hi_v)
        if ind_codes is not None:
            w2 = _project_industry(w, wb_v, ind_codes, float(industry_limit))
            if float(np.linalg.norm(w2 - w, ord=1)) < 1e-10:
                w = w2
                break
            w = w2
        else:
            break

    w = _project_simplex_box(w, lo_v, hi_v)
    weights = {str(sym): float(val) for sym, val in zip(support, w)}
    prev_v = prev.to_numpy(dtype=float)
    turnover = 0.5 * float(np.abs(w - prev_v).sum())
    active_v = w - wb_v
    d_v = d.to_numpy(dtype=float)
    active_risk_proxy = float(np.sqrt(max(0.0, float(np.dot(active_v * active_v, d_v)))))
    return {
        "weights": weights,
        "status": "optimal",
        "turnover": turnover,
        "active_risk_proxy": active_risk_proxy,
    }


def _series(values: Mapping[str, float] | pd.Series | None) -> pd.Series:
    if values is None:
        return pd.Series(dtype=float)
    if isinstance(values, pd.Series):
        series = pd.to_numeric(values, errors="coerce")
    else:
        clean = {}
        for key, value in dict(values).items():
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(parsed):
                clean[str(key)] = parsed
        series = pd.Series(clean, dtype=float)
    return series.replace([np.inf, -np.inf], np.nan).dropna()


def _project_simplex_box(w: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    if float(lo.sum()) > 1.0 + 1e-9:
        return lo / float(lo.sum())
    if float(hi.sum()) < 1.0 - 1e-9:
        return hi / float(hi.sum())

    # Find tau s.t. sum(clip(w0 - tau, lo, hi)) == 1
    w0 = w.copy()
    low, high = -1.0, 1.0
    # Expand bounds until feasible
    for _ in range(40):
        if float(np.clip(w0 - low, lo, hi).sum()) >= 1.0:
            break
        low *= 2.0
    for _ in range(40):
        if float(np.clip(w0 - high, lo, hi).sum()) <= 1.0:
            break
        high *= 2.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        s = float(np.clip(w0 - mid, lo, hi).sum())
        if s > 1.0:
            low = mid
        else:
            high = mid
    return np.clip(w0 - high, lo, hi)


def _project_industry(
    w: np.ndarray,
    wb: np.ndarray,
    industry: np.ndarray,
    limit: float,
) -> np.ndarray:
    out = w.copy()
    for code in sorted(set(industry.tolist())):
        mask = industry == code
        active = float(out[mask].sum() - wb[mask].sum())
        if abs(active) <= limit + 1e-12:
            continue
        target = float(wb[mask].sum()) + (limit if active > 0 else -limit)
        current = float(out[mask].sum())
        if abs(current) <= 1e-12:
            continue
        out[mask] *= target / current
    return out
