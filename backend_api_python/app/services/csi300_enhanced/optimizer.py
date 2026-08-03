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
    size_z: Mapping[str, float] | pd.Series | None = None,
    size_limit: float | None = None,
    te_limit: float | None = None,
) -> dict[str, Any]:
    """
    Solve approximate mean-variance active portfolio with box + simplex (+ industry).

    Unconstrained active weight for diagonal D:
        active_i = alpha_i / (2 * λ * d_i)
    then blend toward previous weights by turn_penalty and project.

    ``te_limit`` uses the same scale as ``active_risk_proxy`` (√(aᵀ D a)); pass
    annualized idio variance in ``idio_var`` for annualized TE interpretation.
    """
    return _optimize_enhanced_index_impl(
        alpha,
        w_bench,
        w_prev=w_prev,
        touch_symbols=None,
        active_limit=active_limit,
        industry=industry,
        industry_limit=industry_limit,
        idio_var=idio_var,
        risk_aversion=risk_aversion,
        turn_penalty=turn_penalty,
        max_iter=max_iter,
        size_z=size_z,
        size_limit=size_limit,
        te_limit=te_limit,
    )


def optimize_enhanced_index_partial(
    alpha: Mapping[str, float] | pd.Series,
    w_bench: Mapping[str, float] | pd.Series,
    w_prev: Mapping[str, float] | pd.Series,
    touch_symbols: Mapping[str, float] | pd.Series | list | set | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Partial rebalance: freeze weights outside ``touch_symbols`` at ``w_prev`` (±1e-6),
    optimize the touch subset under the same constraints as ``optimize_enhanced_index``.
    """
    touch = {str(s) for s in (touch_symbols or [])}
    prev = _series(w_prev)
    if prev.empty:
        return {"weights": {}, "status": "empty", "turnover": 0.0, "active_risk_proxy": 0.0}
    if not touch:
        wb = _series(w_bench).reindex(prev.index).fillna(0.0)
        if float(wb.sum()) > 0:
            wb = wb / float(wb.sum())
        d = _series(kwargs.get("idio_var")).reindex(prev.index).fillna(1.0).clip(lower=1e-6)
        prev_v = prev.to_numpy(dtype=float)
        wb_v = wb.reindex(prev.index).fillna(0.0).to_numpy(dtype=float)
        active_v = prev_v - wb_v
        proxy = float(np.sqrt(max(0.0, float(np.dot(active_v * active_v, d.to_numpy(dtype=float))))))
        weights = {str(k): float(v) for k, v in prev.items()}
        return {
            "weights": weights,
            "status": "no_touch",
            "turnover": 0.0,
            "active_risk_proxy": proxy,
        }
    return _optimize_enhanced_index_impl(
        alpha,
        w_bench,
        w_prev=w_prev,
        touch_symbols=touch,
        **kwargs,
    )


def _optimize_enhanced_index_impl(
    alpha: Mapping[str, float] | pd.Series,
    w_bench: Mapping[str, float] | pd.Series,
    *,
    w_prev: Mapping[str, float] | pd.Series | None = None,
    touch_symbols: set[str] | None = None,
    active_limit: float = 0.025,
    industry: Mapping[str, str] | pd.Series | None = None,
    industry_limit: float = 0.05,
    idio_var: Mapping[str, float] | pd.Series | None = None,
    risk_aversion: float = 1.0,
    turn_penalty: float = 0.01,
    max_iter: int = 80,
    size_z: Mapping[str, float] | pd.Series | None = None,
    size_limit: float | None = None,
    te_limit: float | None = None,
) -> dict[str, Any]:
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
    a = a - float(a.mean())
    active = a / (2.0 * lam * d)
    gam = max(float(turn_penalty), 0.0)
    if gam > 0:
        shrink = 1.0 / (1.0 + gam)
        active = shrink * active + (1.0 - shrink) * (prev - wb)

    lo = (wb - float(active_limit)).clip(lower=0.0)
    hi = (wb + float(active_limit)).clip(upper=1.0)
    if touch_symbols is not None:
        prev_v_series = prev.reindex(support).fillna(0.0)
        for sym in support:
            if str(sym) not in touch_symbols:
                lo.loc[sym] = float(prev_v_series.loc[sym])
                hi.loc[sym] = float(prev_v_series.loc[sym])

    w = (wb + active).clip(lower=lo, upper=hi).to_numpy(dtype=float)
    lo_v = lo.to_numpy(dtype=float)
    hi_v = hi.to_numpy(dtype=float)
    wb_v = wb.to_numpy(dtype=float)
    d_v = d.to_numpy(dtype=float)

    ind_codes = None
    if industry is not None:
        ind_series = pd.Series({str(k): str(v) for k, v in dict(industry).items()})
        ind_codes = ind_series.reindex(support).fillna("UNKNOWN").to_numpy()

    w = _project_all(w, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter))

    status = "optimal" if touch_symbols is None else "partial"
    sz_v = None
    if size_z is not None:
        sz_v = _series(size_z).reindex(support).fillna(0.0).to_numpy(dtype=float)

    if te_limit is not None:
        w, te_degraded = _apply_te_limit(w, wb_v, d_v, float(te_limit))
        if te_degraded:
            status = "degraded_te"
            w = _project_all(w, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter))
            if touch_symbols is not None:
                w = _enforce_frozen_weights(
                    w, support, prev, touch_symbols, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter)
                )

    if sz_v is not None and size_limit is not None:
        w, size_degraded = _project_size(w, wb_v, sz_v, float(size_limit))
        if size_degraded:
            status = "degraded_size"
            w = _project_all(w, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter))
            if touch_symbols is not None:
                w = _enforce_frozen_weights(
                    w, support, prev, touch_symbols, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter)
                )

    if touch_symbols is not None:
        w = _enforce_frozen_weights(
            w, support, prev, touch_symbols, lo_v, hi_v, wb_v, ind_codes, float(industry_limit), int(max_iter)
        )

    weights = {str(sym): float(val) for sym, val in zip(support, w)}
    prev_v = prev.to_numpy(dtype=float)
    turnover = 0.5 * float(np.abs(w - prev_v).sum())
    active_v = w - wb_v
    active_risk_proxy = float(np.sqrt(max(0.0, float(np.dot(active_v * active_v, d_v)))))
    return {
        "weights": weights,
        "status": status,
        "turnover": turnover,
        "active_risk_proxy": active_risk_proxy,
    }


def _enforce_frozen_weights(
    w: np.ndarray,
    support: pd.Index,
    prev: pd.Series,
    touch_symbols: set[str],
    lo: np.ndarray,
    hi: np.ndarray,
    wb: np.ndarray,
    ind_codes: np.ndarray | None,
    industry_limit: float,
    max_iter: int,
) -> np.ndarray:
    """Re-pin non-touch weights to w_prev, then re-project the full simplex."""
    out = w.copy()
    prev_v = prev.reindex(support).fillna(0.0).to_numpy(dtype=float)
    for i, sym in enumerate(support):
        if str(sym) not in touch_symbols:
            out[i] = prev_v[i]
    return _project_all(out, lo, hi, wb, ind_codes, industry_limit, max_iter)


def _project_all(
    w: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    wb: np.ndarray,
    ind_codes: np.ndarray | None,
    industry_limit: float,
    max_iter: int,
) -> np.ndarray:
    for _ in range(max_iter):
        w = _project_simplex_box(w, lo, hi)
        if ind_codes is not None:
            w2 = _project_industry(w, wb, ind_codes, industry_limit)
            if float(np.linalg.norm(w2 - w, ord=1)) < 1e-10:
                w = w2
                break
            w = w2
        else:
            break
    return _project_simplex_box(w, lo, hi)


def _active_risk_proxy(active: np.ndarray, d: np.ndarray) -> float:
    var = float(np.dot(active * active, d))
    return float(np.sqrt(max(0.0, var)))


def _apply_te_limit(
    w: np.ndarray,
    wb: np.ndarray,
    d: np.ndarray,
    te_limit: float,
) -> tuple[np.ndarray, bool]:
    active = w - wb
    proxy = _active_risk_proxy(active, d)
    if proxy <= te_limit + 1e-12:
        return w, False
    scale = te_limit / proxy if proxy > 1e-12 else 0.0
    return wb + scale * active, True


def _project_size(
    w: np.ndarray,
    wb: np.ndarray,
    size_z: np.ndarray,
    limit: float,
) -> tuple[np.ndarray, bool]:
    out = w.copy()
    exposure = float(np.dot(out - wb, size_z))
    if abs(exposure) <= limit + 1e-12:
        return out, False

    degraded = True
    if exposure > limit:
        for side in ("high", "low"):
            mask = size_z > 0 if side == "high" else size_z < 0
            if not np.any(mask):
                continue
            group_exp = float(np.dot(out[mask] - wb[mask], size_z[mask]))
            if group_exp <= 1e-12:
                continue
            excess = exposure - limit
            scale = max(0.0, 1.0 - excess / group_exp)
            out[mask] = wb[mask] + scale * (out[mask] - wb[mask])
            break
    else:
        for side in ("low", "high"):
            mask = size_z < 0 if side == "low" else size_z > 0
            if not np.any(mask):
                continue
            group_exp = float(np.dot(out[mask] - wb[mask], size_z[mask]))
            if group_exp >= -1e-12:
                continue
            deficit = exposure + limit
            scale = max(0.0, 1.0 + deficit / group_exp)
            out[mask] = wb[mask] + scale * (out[mask] - wb[mask])
            break

    return out, degraded


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
