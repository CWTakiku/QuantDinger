"""Cross-sectional factor preprocessing for CSI300 enhanced indexing."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def winsorize_mad(values: Mapping[str, float] | pd.Series, *, n_mad: float = 5.0) -> pd.Series:
    series = _to_series(values)
    if series.empty:
        return series
    median = float(series.median())
    mad = float((series - median).abs().median())
    if mad <= 0 or not np.isfinite(mad):
        return series.copy()
    radius = float(n_mad) * 1.4826 * mad
    return series.clip(lower=median - radius, upper=median + radius)


def cross_section_zscore(values: Mapping[str, float] | pd.Series) -> pd.Series:
    series = _to_series(values)
    if series.empty:
        return series
    std = float(series.std(ddof=0))
    if std <= 0 or not np.isfinite(std):
        return series * 0.0
    return (series - float(series.mean())) / std


def neutralize_industry_size(
    values: Mapping[str, float] | pd.Series,
    industry: Mapping[str, str] | pd.Series,
    log_mcap: Mapping[str, float] | pd.Series,
) -> pd.Series:
    """Return residuals after regressing on industry dummies + log market cap."""
    y = _to_series(values)
    ind = pd.Series({str(k): str(v) for k, v in dict(industry).items()}, dtype="object")
    size = _to_series(log_mcap)
    joined = pd.concat(
        [y.rename("y"), ind.rename("industry"), size.rename("log_mcap")],
        axis=1,
        join="inner",
    ).dropna()
    if len(joined) < 5:
        return cross_section_zscore(y)

    dummies = pd.get_dummies(joined["industry"], prefix="ind", drop_first=True, dtype=float)
    x = pd.concat([joined[["log_mcap"]], dummies], axis=1)
    x = x.assign(const=1.0)
    try:
        beta, *_ = np.linalg.lstsq(x.to_numpy(dtype=float), joined["y"].to_numpy(dtype=float), rcond=None)
        fitted = x.to_numpy(dtype=float) @ beta
        resid = pd.Series(joined["y"].to_numpy(dtype=float) - fitted, index=joined.index)
    except np.linalg.LinAlgError:
        resid = joined["y"] - joined["y"].mean()
    return cross_section_zscore(resid)


def build_equal_weight_alpha(
    factor_frame: pd.DataFrame,
    *,
    signs: Mapping[str, float] | None = None,
) -> pd.Series:
    """Equal-weight combine columns after optional sign flip (e.g. volatility=-1)."""
    if factor_frame is None or factor_frame.empty:
        return pd.Series(dtype=float)
    frame = factor_frame.copy()
    sign_map = {str(k): float(v) for k, v in dict(signs or {}).items()}
    pieces = []
    for column in frame.columns:
        col = pd.to_numeric(frame[column], errors="coerce")
        z = cross_section_zscore(winsorize_mad(col.dropna()))
        z = z.reindex(frame.index)
        pieces.append(z * sign_map.get(str(column), 1.0))
    if not pieces:
        return pd.Series(dtype=float)
    stacked = pd.concat(pieces, axis=1)
    alpha = stacked.mean(axis=1, skipna=True)
    return cross_section_zscore(alpha.dropna())


def _to_series(values: Mapping[str, float] | pd.Series) -> pd.Series:
    if isinstance(values, pd.Series):
        series = pd.to_numeric(values, errors="coerce")
    else:
        clean = {}
        for key, value in dict(values or {}).items():
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(parsed):
                clean[str(key)] = parsed
        series = pd.Series(clean, dtype=float)
    return series.replace([np.inf, -np.inf], np.nan).dropna()
