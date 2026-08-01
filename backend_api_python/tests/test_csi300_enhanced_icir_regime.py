import numpy as np
import pandas as pd

from app.services.csi300_enhanced.layered_alpha import apply_icir_weights, apply_regime


def _make_panel(rng, dates, symbols, layer_bias):
    rows = {}
    for dt in dates:
        base = rng.normal(size=len(symbols))
        rows[dt] = pd.Series(base + layer_bias, index=symbols)
    return pd.DataFrame(rows).T


def test_apply_icir_weights_insufficient_sample_equal_weight():
    rng = np.random.default_rng(0)
    symbols = [f"s{i}" for i in range(20)]
    dates = pd.date_range("2026-01-01", periods=3, freq="B")
    factor_panel = {
        "momentum": _make_panel(rng, dates, symbols, 0.1),
        "risk_liq": _make_panel(rng, dates, symbols, -0.1),
    }
    fwd = pd.DataFrame(
        {sym: rng.normal(scale=0.01, size=len(dates)) for sym in symbols},
        index=dates,
    )
    out = apply_icir_weights(factor_panel, fwd, window=20)
    assert set(out) == {"momentum", "risk_liq"}
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert abs(out["momentum"] - 0.5) < 1e-9
    assert abs(out["risk_liq"] - 0.5) < 1e-9


def test_apply_regime_zeros_momentum_and_renormalizes():
    weights = {
        "momentum": 0.45,
        "risk_liq": 0.25,
        "value_quality": 0.30,
    }
    out = apply_regime(
        weights,
        bench_ret_20=-0.10,
        threshold=-0.08,
        mom_scale=0.0,
    )
    assert abs(out["momentum"]) < 1e-12
    assert abs(out["risk_liq"] - 0.25 / 0.55) < 1e-9
    assert abs(out["value_quality"] - 0.30 / 0.55) < 1e-9
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_apply_regime_not_triggered_preserves_normalized_weights():
    weights = {"momentum": 0.45, "risk_liq": 0.55}
    out = apply_regime(weights, bench_ret_20=-0.02, threshold=-0.08)
    assert abs(out["momentum"] - 0.45) < 1e-9
    assert abs(out["risk_liq"] - 0.55) < 1e-9
