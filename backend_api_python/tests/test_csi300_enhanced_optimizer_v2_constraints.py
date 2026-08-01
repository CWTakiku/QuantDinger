from app.services.csi300_enhanced.optimizer import optimize_enhanced_index


def test_te_limit_shrinks_active_risk():
    alpha = {f"S{i}": float(i) for i in range(10)}
    w_b = {f"S{i}": 0.1 for i in range(10)}
    idio = {f"S{i}": 0.04 for i in range(10)}  # var
    loose = optimize_enhanced_index(alpha, w_b, idio_var=idio, risk_aversion=0.05, turn_penalty=0.0)
    tight = optimize_enhanced_index(
        alpha, w_b, idio_var=idio, risk_aversion=0.05, turn_penalty=0.0, te_limit=0.01
    )
    assert tight["active_risk_proxy"] <= loose["active_risk_proxy"] + 1e-9
    assert tight["active_risk_proxy"] <= 0.01 + 1e-6


def test_size_limit_bounds_exposure():
    alpha = {"A": 2.0, "B": -2.0, "C": 0.0}
    w_b = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    size_z = {"A": 2.0, "B": -2.0, "C": 0.0}
    result = optimize_enhanced_index(
        alpha, w_b, size_z=size_z, size_limit=0.1, turn_penalty=0.0, risk_aversion=0.1
    )
    w = result["weights"]
    exposure = sum(w[k] * size_z[k] for k in w) - sum(w_b[k] * size_z[k] for k in w_b)
    assert abs(exposure) <= 0.1 + 1e-6
