"""CSI300 Enhanced Index QP 2.0
Long-only CSI300 enhanced index 2.0: layered neutralized alpha (momentum/vol/value),
PIT bench weights, industry/size/TE-constrained weekly QP.

C1: momentum + realized vol + EP/BP (when fundamentals available). Industry/size
neutralization uses injected maps; missing layers are re-weighted automatically.
"""

# @param universe_top_n int 50 Cap CSI300 names by index weight for faster backtests range=20:300:10
# @param active_limit float 0.025 Max active weight vs bench range=0.01:0.05:0.005
# @param risk_aversion float 1.0 Risk aversion lambda range=0.1:5.0:0.1
# @param turn_penalty float 0.01 Turnover soft penalty range=0.0:0.1:0.005
# @param min_turnover float 0.02 Skip rebalance if turnover below this range=0.0:0.1:0.01
# @param mom_fast int 60 Fast momentum lookback range=20:120:5
# @param mom_slow int 120 Slow momentum lookback range=60:250:10
# @param vol_period int 20 Realized vol lookback range=10:60:5
# @param min_history_bars int 0 Min bars to enter alpha pool; 0 => mom_slow+1 range=0:260:1
# @param industry_limit float 0.05 Max active industry weight vs bench range=0.01:0.15:0.005
# @param size_limit float 0.30 Max abs size exposure (active · size_z) range=0.05:1.0:0.05
# @param te_limit float 0.08 Soft/hard TE proxy cap (√(aᵀDa)) range=0.01:0.25:0.01
# @param w_momentum float 0.45 Momentum layer weight range=0.0:1.0:0.05
# @param w_risk_liq float 0.25 Low-vol / risk layer weight range=0.0:1.0:0.05
# @param w_value_quality float 0.30 Value (EP/BP) layer weight range=0.0:1.0:0.05
# @param w_flow float 0.0 Flow layer weight (C2; unused in C1) range=0.0:1.0:0.05
# @param w_consensus float 0.0 Consensus layer weight (C2; unused in C1) range=0.0:1.0:0.05

import numpy as np
import pandas as pd


def _min_history_bars(params):
    params = params or {}
    mom_slow = int(params.get("mom_slow", 120))
    configured = int(params.get("min_history_bars", 0) or 0)
    if configured > 0:
        return configured
    return mom_slow + 1


def _eligible_symbols(symbols, min_bars):
    """Keep names with enough visible history (listing / resume soft filter)."""
    if not symbols or min_bars <= 0:
        return list(symbols or [])
    hist = get_history(min_bars, "1d", "close", symbols)
    if hist is None:
        return []
    if isinstance(hist, dict):
        eligible = []
        for sym in symbols:
            frame = hist.get(sym)
            if frame is not None and len(frame) >= min_bars:
                eligible.append(sym)
        return eligible
    if len(symbols) == 1 and len(hist) >= min_bars:
        return [symbols[0]]
    return []


def _winsorize_mad(series, n_mad=5.0):
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return series
    med = float(series.median())
    mad = float((series - med).abs().median())
    if mad <= 0:
        return series
    radius = float(n_mad) * 1.4826 * mad
    return series.clip(med - radius, med + radius)


def _zscore(series):
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty:
        return series
    std = float(series.std(ddof=0))
    if std <= 0:
        return series * 0.0
    return (series - float(series.mean())) / std


def _neutralize_industry_size(raw, industry, log_mcap):
    """Residuals after industry dummies + log mcap; falls back to z-score."""
    y = _zscore(_winsorize_mad(raw))
    if y.empty:
        return y
    ind = pd.Series({str(k): str(v) for k, v in dict(industry or {}).items()}, dtype="object")
    size = pd.to_numeric(pd.Series(log_mcap), errors="coerce")
    joined = pd.concat(
        [y.rename("y"), ind.rename("industry"), size.rename("log_mcap")],
        axis=1,
        join="inner",
    ).dropna()
    if len(joined) < 5:
        return _zscore(y)
    dummies = pd.get_dummies(joined["industry"], prefix="ind", drop_first=True, dtype=float)
    x = pd.concat([joined[["log_mcap"]], dummies], axis=1)
    x = x.assign(const=1.0)
    try:
        beta, *_ = np.linalg.lstsq(
            x.to_numpy(dtype=float),
            joined["y"].to_numpy(dtype=float),
            rcond=None,
        )
        fitted = x.to_numpy(dtype=float) @ beta
        resid = pd.Series(joined["y"].to_numpy(dtype=float) - fitted, index=joined.index)
    except Exception:
        resid = joined["y"] - float(joined["y"].mean())
    return _zscore(resid)


def _combine_layers(layer_scores, weights):
    """Weighted combine; reallocate weights from missing / empty layers."""
    active = {}
    for name, weight in (weights or {}).items():
        if float(weight) <= 0:
            continue
        series = layer_scores.get(name)
        if series is None:
            continue
        values = pd.to_numeric(series, errors="coerce").dropna()
        if values.empty:
            continue
        active[name] = float(weight)
    if not active:
        return pd.Series(dtype=float)
    total = sum(active.values())
    pieces = []
    for name, weight in active.items():
        pieces.append(pd.to_numeric(layer_scores[name], errors="coerce") * (weight / total))
    combined = pd.concat(pieces, axis=1).sum(axis=1, min_count=1)
    return _zscore(combined.dropna())


def _cap_universe(symbols, top_n, as_of):
    """Optional cap by PIT bench weight (largest first)."""
    if not symbols or top_n <= 0 or top_n >= len(symbols):
        return list(symbols or [])
    try:
        weights = get_csi300_bench_weights(as_of, symbols=list(symbols))
    except Exception:
        return list(symbols)[:top_n]
    ranked = sorted(symbols, key=lambda s: float((weights or {}).get(s, 0.0)), reverse=True)
    return ranked[:top_n]


def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(140)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    g.alpha = {}
    g.idio_var = {}
    g.last_weights = {}
    run_daily(update_alpha, time="15:05")
    run_weekly(rebalance, weekday=1, time="09:35")


def update_alpha(context, data):
    symbols = get_universe_stocks()
    if not symbols:
        return
    as_of = str(context.current_dt.date())
    top_n = int(context.params.get("universe_top_n", 50))
    symbols = _cap_universe(symbols, top_n, as_of)

    mom_fast = int(context.params.get("mom_fast", 60))
    mom_slow = int(context.params.get("mom_slow", 120))
    vol_period = int(context.params.get("vol_period", 20))
    min_bars = _min_history_bars(context.params)
    eligible = _eligible_symbols(symbols, min_bars)
    excluded = len(symbols) - len(eligible)
    if excluded > 0:
        log("universe filter: eligible=%d excluded_short_history=%d min_bars=%d" % (
            len(eligible), excluded, min_bars,
        ))
    if len(eligible) < 10:
        return

    try:
        industry = get_ashare_industry_map(eligible, as_of)
    except Exception as exc:
        log("industry map skipped: %s" % exc)
        industry = {}
    try:
        log_mcap = get_ashare_size_log_mcap(eligible, as_of)
    except Exception as exc:
        log("size panel skipped: %s" % exc)
        log_mcap = pd.Series(dtype=float)

    try:
        fast = get_factors(eligible, "momentum", period=mom_fast)
        slow = get_factors(eligible, "momentum", period=mom_slow)
        vol = get_factors(eligible, "realized_volatility", period=vol_period)
    except Exception as exc:
        log("update_alpha factors skipped: %s" % exc)
        return
    if fast is None or slow is None or vol is None:
        return
    if fast.empty or slow.empty or vol.empty:
        return

    mom_fast_s = fast["momentum"] if "momentum" in fast.columns else pd.Series(dtype=float)
    mom_slow_s = slow["momentum"] if "momentum" in slow.columns else pd.Series(dtype=float)
    vol_s = vol["realized_volatility"] if "realized_volatility" in vol.columns else pd.Series(dtype=float)
    mom_raw = (pd.to_numeric(mom_fast_s, errors="coerce") + pd.to_numeric(mom_slow_s, errors="coerce")) / 2.0
    risk_raw = -pd.to_numeric(vol_s, errors="coerce")

    layer_scores = {
        "momentum": _neutralize_industry_size(mom_raw, industry, log_mcap),
        "risk_liq": _neutralize_industry_size(risk_raw, industry, log_mcap),
        "value_quality": pd.Series(dtype=float),
        "flow": pd.Series(dtype=float),
        "consensus": pd.Series(dtype=float),
    }

    try:
        fund = get_fundamentals(["PE", "PB"], eligible)
    except Exception as exc:
        log("fundamentals skipped: %s" % exc)
        fund = None
    if fund is not None and not fund.empty:
        pe = pd.to_numeric(fund.get("PE"), errors="coerce")
        pb = pd.to_numeric(fund.get("PB"), errors="coerce")
        if pe is None:
            pe = pd.Series(dtype=float)
        if pb is None:
            pb = pd.Series(dtype=float)
        ep = 1.0 / pe.replace(0, np.nan)
        bp = 1.0 / pb.replace(0, np.nan)
        value_raw = pd.concat([ep.rename("ep"), bp.rename("bp")], axis=1).mean(axis=1, skipna=True)
        if value_raw.dropna().shape[0] >= 10:
            layer_scores["value_quality"] = _neutralize_industry_size(value_raw, industry, log_mcap)

    weights = {
        "momentum": float(context.params.get("w_momentum", 0.45)),
        "risk_liq": float(context.params.get("w_risk_liq", 0.25)),
        "value_quality": float(context.params.get("w_value_quality", 0.30)),
        "flow": float(context.params.get("w_flow", 0.0)),
        "consensus": float(context.params.get("w_consensus", 0.0)),
    }
    alpha = _combine_layers(layer_scores, weights)
    if len(alpha) < 10:
        return

    g.alpha = {str(k): float(v) for k, v in alpha.items()}
    idio = {}
    for sym, vol_val in pd.to_numeric(vol_s, errors="coerce").dropna().items():
        key = str(sym)
        if key in g.alpha:
            idio[key] = max(float(vol_val) ** 2, 1e-6)
    g.idio_var = idio


def rebalance(context, data):
    alpha = dict(g.alpha or {})
    if len(alpha) < 10:
        return

    symbols = list(alpha.keys())
    as_of = str(context.current_dt.date())
    n = len(symbols)
    equal = {sym: 1.0 / n for sym in symbols}
    bench_source = "equal_fallback"
    try:
        w_bench = get_csi300_bench_weights(as_of, symbols=symbols)
        if w_bench and abs(sum(w_bench.values()) - 1.0) < 1e-4:
            bench_source = "csi300_pit"
        else:
            w_bench = dict(equal)
            bench_source = "equal_fallback"
    except Exception as exc:
        log("bench weights fallback: %s" % exc)
        w_bench = dict(equal)
        bench_source = "equal_fallback"

    try:
        industry = get_ashare_industry_map(symbols, as_of)
    except Exception as exc:
        log("industry map skipped: %s" % exc)
        industry = {}
    try:
        log_mcap = get_ashare_size_log_mcap(symbols, as_of)
    except Exception as exc:
        log("size panel skipped: %s" % exc)
        log_mcap = pd.Series(dtype=float)

    size_z = _zscore(log_mcap) if log_mcap is not None and len(log_mcap) > 0 else pd.Series(dtype=float)
    w_prev = dict(g.last_weights) if g.last_weights else dict(w_bench)
    idio_var = dict(g.idio_var or {})
    if not idio_var:
        idio_var = {sym: 1.0 for sym in symbols}

    industry_limit = float(context.params.get("industry_limit", 0.05))
    size_limit = float(context.params.get("size_limit", 0.30))
    te_limit = float(context.params.get("te_limit", 0.08))

    result = optimize_enhanced_index(
        alpha,
        w_bench,
        w_prev=w_prev,
        active_limit=float(context.params.get("active_limit", 0.025)),
        risk_aversion=float(context.params.get("risk_aversion", 1.0)),
        turn_penalty=float(context.params.get("turn_penalty", 0.01)),
        industry=industry or None,
        industry_limit=industry_limit,
        size_z=size_z if len(size_z) else None,
        size_limit=size_limit,
        te_limit=te_limit,
        idio_var=idio_var,
    )
    weights = result.get("weights") or {}
    if not weights:
        return

    min_turn = float(context.params.get("min_turnover", 0.02))
    if float(result.get("turnover") or 0.0) < min_turn:
        log("skip rebalance: turnover=%.4f < %.4f" % (result.get("turnover") or 0.0, min_turn))
        return

    current = get_positions()
    for symbol in current:
        if symbol not in weights:
            order_target_percent(symbol, 0.0, reason="ei_exit")

    for symbol, weight in weights.items():
        order_target_percent(symbol, float(weight), reason="ei_target")

    g.last_weights = {str(k): float(v) for k, v in weights.items() if float(v) > 1e-8}
    log(
        "ei2 rebalance names=%d turnover=%.4f te=%.4f status=%s bench_source=%s industry=%d size=%d"
        % (
            len(g.last_weights),
            result.get("turnover") or 0.0,
            result.get("active_risk_proxy") or 0.0,
            result.get("status") or "",
            bench_source,
            len(industry or {}),
            len(size_z) if size_z is not None else 0,
        )
    )
