from math import sqrt

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


def test_te_limit_bites_under_annualized_idio():
    """Annualized idio ``(vol·√252)²`` puts default te_limit=0.08 on a binding scale."""
    alpha = {f"S{i}": float(20 - i) for i in range(10)}
    w_b = {f"S{i}": 0.1 for i in range(10)}
    daily_vol = 0.02
    idio = {f"S{i}": (daily_vol * sqrt(252)) ** 2 for i in range(10)}
    # Wider active box so TE (not box) is the binding constraint for the check.
    loose = optimize_enhanced_index(
        alpha, w_b, idio_var=idio, risk_aversion=0.05, turn_penalty=0.0, active_limit=0.15
    )
    tight = optimize_enhanced_index(
        alpha,
        w_b,
        idio_var=idio,
        risk_aversion=0.05,
        turn_penalty=0.0,
        active_limit=0.15,
        te_limit=0.08,
    )
    assert loose["active_risk_proxy"] > 0.08
    assert tight["active_risk_proxy"] <= 0.08 + 1e-6
    assert tight["active_risk_proxy"] < loose["active_risk_proxy"] - 1e-9


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
