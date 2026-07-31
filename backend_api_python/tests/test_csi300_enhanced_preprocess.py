import numpy as np
import pandas as pd

from app.services.csi300_enhanced.preprocess import (
    build_equal_weight_alpha,
    cross_section_zscore,
    neutralize_industry_size,
    winsorize_mad,
)


def test_winsorize_mad_clips_outlier():
    values = {f"s{i}": float(i) for i in range(20)}
    values["outlier"] = 1e6
    clipped = winsorize_mad(values, n_mad=3)
    assert clipped["outlier"] < 1e6
    assert clipped["outlier"] > clipped.median()


def test_zscore_zero_mean_unit_std():
    values = {f"s{i}": float(i) for i in range(10)}
    z = cross_section_zscore(values)
    assert abs(float(z.mean())) < 1e-9
    assert abs(float(z.std(ddof=0)) - 1.0) < 1e-9


def test_neutralize_removes_size_trend():
    rng = np.random.default_rng(0)
    symbols = [f"s{i}" for i in range(40)]
    industry = {s: ("A" if i < 20 else "B") for i, s in enumerate(symbols)}
    log_mcap = {s: float(i) for i, s in enumerate(symbols)}
    # Factor mostly equals size plus noise
    values = {s: log_mcap[s] + 0.01 * float(rng.normal()) for s in symbols}
    resid = neutralize_industry_size(values, industry, log_mcap)
    corr = pd.Series(resid).corr(pd.Series(log_mcap), method="pearson")
    assert abs(float(corr)) < 0.25


def test_equal_weight_alpha_combines_signed_factors():
    frame = pd.DataFrame({
        "mom": {"a": 2.0, "b": 0.0, "c": -2.0},
        "vol": {"a": 0.5, "b": 1.0, "c": 1.5},
    })
    alpha = build_equal_weight_alpha(frame, signs={"vol": -1.0})
    assert set(alpha.index) == {"a", "b", "c"}
    # High momentum and low vol should rank a above c.
    assert alpha["a"] > alpha["c"]
