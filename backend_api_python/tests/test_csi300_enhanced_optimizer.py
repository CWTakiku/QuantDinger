from app.services.csi300_enhanced.optimizer import optimize_enhanced_index


def test_optimize_respects_budget_and_long_only():
    alpha = {f"s{i}": float(10 - i) for i in range(10)}
    w_bench = {f"s{i}": 0.1 for i in range(10)}
    result = optimize_enhanced_index(alpha, w_bench, active_limit=0.03, risk_aversion=0.5)
    weights = result["weights"]
    assert result["status"] == "optimal"
    assert abs(sum(weights.values()) - 1.0) < 1e-5
    assert min(weights.values()) >= -1e-9
    for sym, w in weights.items():
        assert abs(w - w_bench[sym]) <= 0.03 + 1e-6


def test_optimize_prefers_higher_alpha_within_active_band():
    alpha = {"a": 2.0, "b": -2.0, "c": 0.0}
    w_bench = {"a": 0.3, "b": 0.3, "c": 0.4}
    result = optimize_enhanced_index(alpha, w_bench, active_limit=0.05, risk_aversion=0.1, turn_penalty=0.0)
    assert result["weights"]["a"] >= result["weights"]["b"]


def test_industry_limit_soft_projection():
    alpha = {f"s{i}": 1.0 for i in range(6)}
    w_bench = {f"s{i}": 1.0 / 6 for i in range(6)}
    industry = {f"s{i}": ("X" if i < 3 else "Y") for i in range(6)}
    # Push all alpha to industry X names
    for i in range(3):
        alpha[f"s{i}"] = 5.0
    for i in range(3, 6):
        alpha[f"s{i}"] = -5.0
    result = optimize_enhanced_index(
        alpha,
        w_bench,
        industry=industry,
        industry_limit=0.04,
        active_limit=0.2,
        risk_aversion=0.05,
        turn_penalty=0.0,
    )
    w = result["weights"]
    active_x = sum(w[f"s{i}"] for i in range(3)) - sum(w_bench[f"s{i}"] for i in range(3))
    assert abs(active_x) <= 0.04 + 1e-3
