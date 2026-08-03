from app.services.csi300_enhanced.optimizer import (
    optimize_enhanced_index,
    optimize_enhanced_index_partial,
)


def test_partial_freeze_keeps_non_touch_weights():
    symbols = [f"S{i}" for i in range(6)]
    alpha = {sym: float(i - 2) for i, sym in enumerate(symbols)}
    w_b = {sym: 1.0 / len(symbols) for sym in symbols}
    w_prev = {sym: 1.0 / len(symbols) for sym in symbols}
    w_prev["S0"] = 0.22
    w_prev["S1"] = 0.10

    touch = ["S2", "S3", "S4", "S5"]
    result = optimize_enhanced_index_partial(
        alpha,
        w_b,
        w_prev,
        touch,
        turn_penalty=0.0,
        risk_aversion=0.2,
        active_limit=0.05,
    )
    weights = result["weights"]
    assert result["status"] == "partial"
    for sym in ("S0", "S1"):
        assert abs(weights[sym] - w_prev[sym]) <= 1e-6
    assert abs(sum(weights.values()) - 1.0) <= 1e-6


def test_partial_empty_touch_returns_prev():
    alpha = {"A": 1.0, "B": -1.0}
    w_b = {"A": 0.5, "B": 0.5}
    w_prev = {"A": 0.6, "B": 0.4}
    result = optimize_enhanced_index_partial(alpha, w_b, w_prev, [])
    assert result["status"] == "no_touch"
    assert result["turnover"] == 0.0
    assert abs(result["weights"]["A"] - 0.6) <= 1e-6
    assert abs(result["weights"]["B"] - 0.4) <= 1e-6


def test_partial_matches_full_when_all_touch():
    alpha = {f"S{i}": float(i) for i in range(8)}
    w_b = {f"S{i}": 0.125 for i in range(8)}
    w_prev = dict(w_b)
    kwargs = dict(
        turn_penalty=0.0,
        risk_aversion=0.3,
        active_limit=0.03,
        te_limit=0.05,
    )
    full = optimize_enhanced_index(alpha, w_b, w_prev=w_prev, **kwargs)
    partial = optimize_enhanced_index_partial(
        alpha,
        w_b,
        w_prev,
        list(alpha.keys()),
        **kwargs,
    )
    for sym in alpha:
        assert abs(full["weights"][sym] - partial["weights"][sym]) <= 1e-5
