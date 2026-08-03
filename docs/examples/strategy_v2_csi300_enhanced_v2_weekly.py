"""CSI300 Enhanced Index QP 2.0
Long-only CSI300 enhanced index 2.0: layered neutralized alpha (momentum/vol/value/flow/consensus),
PIT bench weights, industry/size/TE-constrained weekly QP.

C2: momentum + realized vol + EP/BP + local flow/consensus panels. Industry/size
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
# @param w_momentum float 0.35 Momentum layer weight range=0.0:1.0:0.05
# @param w_risk_liq float 0.20 Low-vol / risk layer weight range=0.0:1.0:0.05
# @param w_value_quality float 0.20 Value (EP/BP) layer weight range=0.0:1.0:0.05
# @param w_flow float 0.15 Northbound / margin flow layer weight range=0.0:1.0:0.05
# @param w_consensus float 0.10 Analyst consensus layer weight range=0.0:1.0:0.05
# @param use_icir bool false Enable ICIR layer weighting (needs rolling history) range=
# @param icir_window int 20 ICIR rolling window (trading days) range=10:60:5
# @param regime_enabled bool true Enable benchmark drawdown regime filter range=
# @param regime_ret_threshold float -0.08 20D benchmark return trigger range=-0.20:0.0:0.01
# @param partial_rebalance_enabled bool true Enable daily partial rebalance monitor range=
# @param partial_active_dev_trigger float 0.015 Trigger partial when |active| exceeds this range=0.005:0.03:0.005
# @param partial_te_trigger float 0.08 Trigger partial when ex-ante TE exceeds this range=0.01:0.25:0.01
# @param partial_min_turnover float 0.005 Min turnover for partial rebalance range=0.0:0.05:0.001
# @param partial_max_touch int 10 Max symbols in partial touch set range=3:30:1

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


def _canon_cn_key(value):
    """Normalize CNStock keys to ``CNStock:600519.SH`` without external imports."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    market, symbol = (raw.split(":", 1) + [""])[:2] if ":" in raw else ("CNStock", raw)
    if market.upper() != "CNSTOCK":
        return raw
    sym = str(symbol or "").strip().upper()
    if sym.endswith(".SH") or sym.endswith(".SZ"):
        return f"CNStock:{sym}"
    if sym.isdigit() and len(sym) == 6:
        return f"CNStock:{sym}.SH" if sym.startswith("6") else f"CNStock:{sym}.SZ"
    return f"CNStock:{sym}" if sym else raw


def _cap_universe(symbols, top_n, as_of):
    """Optional cap by PIT bench weight (largest first)."""
    if not symbols or top_n <= 0 or top_n >= len(symbols):
        return list(symbols or [])
    try:
        weights = get_csi300_bench_weights(as_of, symbols=list(symbols))
        ranked = sorted(
            symbols,
            key=lambda s: float((weights or {}).get(_canon_cn_key(s), 0.0)),
            reverse=True,
        )
        return ranked[:top_n]
    except Exception:
        return list(symbols)[:top_n]


def _bench_ret_20(benchmark="CNStock:000300.SH"):
    """Benchmark total return over the last 20 completed daily bars."""
    try:
        hist = get_history(21, "1d", "close", [benchmark])
    except Exception:
        return 0.0
    if hist is None:
        return 0.0
    if isinstance(hist, dict):
        frame = hist.get(benchmark)
    else:
        frame = hist
    if frame is None or len(frame) < 21:
        return 0.0
    close = pd.to_numeric(frame["close"] if "close" in frame.columns else frame.iloc[:, 0], errors="coerce").dropna()
    if len(close) < 21:
        return 0.0
    start = float(close.iloc[0])
    end = float(close.iloc[-1])
    if start <= 0:
        return 0.0
    return end / start - 1.0


def _append_icir_panel(panel, layer_scores, as_of, max_rows=80):
    """Rolling store of daily layer score cross-sections for ICIR."""
    panel = dict(panel or {})
    row = {
        name: pd.to_numeric(series, errors="coerce")
        for name, series in (layer_scores or {}).items()
        if series is not None and len(pd.to_numeric(series, errors="coerce").dropna()) >= 5
    }
    if not row:
        return panel
    dt = pd.Timestamp(as_of)
    for name, series in row.items():
        series = pd.to_numeric(series, errors="coerce")
        if not isinstance(series, pd.Series) or series.empty:
            continue
        # Avoid ``empty.loc[dt] = series`` — pandas raises
        # "cannot set a frame with no defined columns".
        piece = series.to_frame().T
        piece.index = [dt]
        frame = panel.get(name)
        if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
            frame = piece
        else:
            frame = pd.concat([frame, piece], axis=0)
            frame = frame[~frame.index.duplicated(keep="last")]
        panel[name] = frame.sort_index().tail(max_rows)
    return panel


def _forward_return_panel(symbols, as_of, lookback=80):
    """One-day forward return panel aligned to layer history dates.

    Returns are labeled on day T as the move from T to T+1. At decision time
    ``as_of`` that path is unknown, so rows with index >= as_of are excluded.
    """
    if not symbols:
        return pd.DataFrame()
    try:
        hist = get_history(int(lookback) + 2, "1d", "close", list(symbols))
    except Exception:
        return pd.DataFrame()
    if hist is None:
        return pd.DataFrame()
    pieces = []
    if isinstance(hist, dict):
        for sym in symbols:
            frame = hist.get(sym)
            if frame is None or len(frame) < 3:
                continue
            close = pd.to_numeric(frame["close"], errors="coerce")
            pieces.append((sym, close.pct_change().shift(-1)))
    elif len(symbols) == 1 and len(hist) >= 3:
        close = pd.to_numeric(hist["close"], errors="coerce")
        pieces.append((symbols[0], close.pct_change().shift(-1)))
    if not pieces:
        return pd.DataFrame()
    out = pd.concat({sym: series for sym, series in pieces}, axis=1).sort_index()
    if as_of:
        out = out.loc[out.index < pd.Timestamp(as_of)]
    return out.tail(lookback)


def _resolve_layer_weights(context, layer_scores, as_of):
    """Base param weights with optional ICIR and regime overlays."""
    weights = {
        "momentum": float(context.params.get("w_momentum", 0.35)),
        "risk_liq": float(context.params.get("w_risk_liq", 0.20)),
        "value_quality": float(context.params.get("w_value_quality", 0.20)),
        "flow": float(context.params.get("w_flow", 0.15)),
        "consensus": float(context.params.get("w_consensus", 0.10)),
    }
    use_icir = bool(context.params.get("use_icir", False))
    if use_icir:
        g.icir_panel = _append_icir_panel(g.icir_panel, layer_scores, as_of)
        mom_scores = layer_scores.get("momentum", pd.Series(dtype=float))
        mom_symbols = list(mom_scores.index) if isinstance(mom_scores, pd.Series) else []
        fwd = _forward_return_panel(mom_symbols, as_of)
        if g.icir_panel and not fwd.empty:
            icir_weights = apply_icir_weights(
                g.icir_panel,
                fwd,
                window=int(context.params.get("icir_window", 20)),
            )
            if icir_weights:
                weights = {name: float(icir_weights.get(name, weights.get(name, 0.0))) for name in weights}
    if bool(context.params.get("regime_enabled", True)):
        weights = apply_regime(
            weights,
            bench_ret_20=_bench_ret_20(),
            threshold=float(context.params.get("regime_ret_threshold", -0.08)),
            mom_scale=0.0,
        )
    return weights


def _active_risk_proxy(weights, w_bench, idio_var):
    """Ex-ante TE proxy sqrt(a' D a) on shared support."""
    symbols = set(weights or {}) | set(w_bench or {})
    var = 0.0
    for sym in symbols:
        active = float((weights or {}).get(sym, 0.0)) - float((w_bench or {}).get(sym, 0.0))
        d = float((idio_var or {}).get(sym, 1.0))
        var += active * active * d
    return float(var ** 0.5)


def _select_touch_symbols(w_prev, w_bench, idio_var, params):
    """Pick symbols to touch when deviation or TE breach thresholds."""
    dev_trigger = float(params.get("partial_active_dev_trigger", 0.015))
    te_limit = float(params.get("te_limit", 0.08))
    te_trigger = float(params.get("partial_te_trigger", te_limit))
    max_touch = int(params.get("partial_max_touch", 10))
    active = {
        sym: float(w_prev.get(sym, 0.0)) - float(w_bench.get(sym, 0.0))
        for sym in set(w_prev or {}) | set(w_bench or {})
    }
    te = _active_risk_proxy(w_prev, w_bench, idio_var)
    touch = {sym for sym, val in active.items() if abs(val) > dev_trigger}
    if te > te_trigger:
        ranked = sorted(active.items(), key=lambda item: abs(item[1]), reverse=True)
        for sym, _ in ranked:
            touch.add(sym)
            if len(touch) >= max_touch:
                break
    if len(touch) > max_touch:
        ranked = sorted(touch, key=lambda sym: abs(active.get(sym, 0.0)), reverse=True)
        touch = set(ranked[:max_touch])
    return list(touch), te, active


def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(140)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    g.alpha = {}
    g.idio_var = {}
    g.last_weights = {}
    g.icir_panel = {}
    run_daily(update_alpha_and_monitor, time="15:05")
    run_weekly(rebalance, weekday=1, time="09:35")


def update_alpha_and_monitor(context, data):
    update_alpha(context, data)
    monitor_partial_rebalance(context, data)


def monitor_partial_rebalance(context, data):
    if not bool(context.params.get("partial_rebalance_enabled", True)):
        return
    alpha = dict(g.alpha or {})
    w_prev = dict(g.last_weights or {})
    if len(alpha) < 10 or len(w_prev) < 10:
        return

    symbols = list(alpha.keys())
    as_of = str(context.current_dt.date())
    n = len(symbols)
    equal = {sym: 1.0 / n for sym in symbols}
    bench_source = "equal_weight"
    try:
        w_bench, bench_source = get_csi300_bench_weights_with_meta(as_of, symbols=symbols)
        if not w_bench:
            w_bench = dict(equal)
            bench_source = "equal_weight"
    except Exception:
        w_bench = dict(equal)
        bench_source = "equal_weight"

    idio_var = dict(g.idio_var or {})
    if not idio_var:
        idio_var = {sym: 1.0 for sym in symbols}

    dev_trigger = float(context.params.get("partial_active_dev_trigger", 0.015))
    te_limit = float(context.params.get("te_limit", 0.08))
    te_trigger = float(context.params.get("partial_te_trigger", te_limit))
    touch, te, active = _select_touch_symbols(w_prev, w_bench, idio_var, context.params)
    max_dev = max((abs(v) for v in active.values()), default=0.0)
    if max_dev <= dev_trigger and te <= te_trigger:
        return
    if not touch:
        return

    try:
        industry = get_ashare_industry_map(symbols, as_of)
    except Exception:
        industry = {}
    try:
        log_mcap = get_ashare_size_log_mcap(symbols, as_of)
    except Exception:
        log_mcap = pd.Series(dtype=float)
    size_z = _zscore(log_mcap) if isinstance(log_mcap, pd.Series) and len(log_mcap) > 0 else pd.Series(dtype=float)
    size_z_map = {str(k): float(v) for k, v in size_z.items()} if len(size_z) else None

    result = optimize_enhanced_index_partial(
        alpha,
        w_bench,
        w_prev,
        touch,
        active_limit=float(context.params.get("active_limit", 0.025)),
        risk_aversion=float(context.params.get("risk_aversion", 1.0)),
        turn_penalty=float(context.params.get("turn_penalty", 0.01)),
        industry=industry if industry else None,
        industry_limit=float(context.params.get("industry_limit", 0.05)),
        size_z=size_z_map,
        size_limit=float(context.params.get("size_limit", 0.30)),
        te_limit=float(context.params.get("te_limit", 0.08)),
        idio_var=idio_var,
    )
    weights = result.get("weights") or {}
    if not weights:
        return

    min_turn = float(context.params.get("partial_min_turnover", 0.005))
    if float(result.get("turnover") or 0.0) < min_turn:
        log(
            "skip partial: turnover=%.4f < %.4f touch=%d"
            % (result.get("turnover") or 0.0, min_turn, len(touch))
        )
        return

    current = get_positions()
    for symbol in current:
        if symbol not in weights:
            order_target_percent(symbol, 0.0, reason="ei_partial_exit")
    for symbol, weight in weights.items():
        order_target_percent(symbol, float(weight), reason="ei_partial_target")

    g.last_weights = {str(k): float(v) for k, v in weights.items() if float(v) > 1e-8}
    record_enhanced_index_diagnostics(
        as_of=as_of,
        weights=g.last_weights,
        w_bench=w_bench,
        optimize_result=result,
        bench_source=bench_source,
        industry=industry,
        size_z=size_z_map,
        kind="partial",
    )
    log(
        "ei2 partial names=%d touch=%d turnover=%.4f te=%.4f max_dev=%.4f status=%s"
        % (
            len(g.last_weights),
            len(touch),
            result.get("turnover") or 0.0,
            result.get("active_risk_proxy") or 0.0,
            max_dev,
            result.get("status") or "",
        )
    )


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
        fund = get_ashare_valuation_panel(eligible, as_of)
    except Exception as exc:
        log("valuation panel skipped: %s" % exc)
        fund = None
    if fund is not None and not fund.empty:
        pe = pd.to_numeric(fund["PE"], errors="coerce") if "PE" in fund.columns else pd.Series(dtype=float)
        pb = pd.to_numeric(fund["PB"], errors="coerce") if "PB" in fund.columns else pd.Series(dtype=float)
        if not isinstance(pe, pd.Series):
            pe = pd.Series(dtype=float)
        if not isinstance(pb, pd.Series):
            pb = pd.Series(dtype=float)
        ep = 1.0 / pe.replace(0, np.nan)
        bp = 1.0 / pb.replace(0, np.nan)
        value_raw = pd.concat([ep.rename("ep"), bp.rename("bp")], axis=1).mean(axis=1, skipna=True)
        if value_raw.dropna().shape[0] >= 10:
            layer_scores["value_quality"] = _neutralize_industry_size(value_raw, industry, log_mcap)

    try:
        flow_raw = get_ashare_flow_panel(eligible, as_of)
    except Exception as exc:
        log("flow panel skipped: %s" % exc)
        flow_raw = pd.Series(dtype=float)
    if flow_raw is not None and len(pd.to_numeric(flow_raw, errors="coerce").dropna()) >= 10:
        layer_scores["flow"] = _neutralize_industry_size(flow_raw, industry, log_mcap)

    try:
        consensus_raw = get_ashare_consensus_panel(eligible, as_of)
    except Exception as exc:
        log("consensus panel skipped: %s" % exc)
        consensus_raw = pd.Series(dtype=float)
    if consensus_raw is not None and len(pd.to_numeric(consensus_raw, errors="coerce").dropna()) >= 10:
        layer_scores["consensus"] = _neutralize_industry_size(consensus_raw, industry, log_mcap)

    weights = _resolve_layer_weights(context, layer_scores, as_of)
    alpha = _combine_layers(layer_scores, weights)
    if len(alpha) < 10:
        return

    g.alpha = {str(k): float(v) for k, v in alpha.items()}
    idio = {}
    for sym, vol_val in pd.to_numeric(vol_s, errors="coerce").dropna().items():
        key = str(sym)
        if key in g.alpha:
            # Annualize daily realized vol so te_limit≈0.08 is on TE scale.
            idio[key] = max((float(vol_val) * (252.0 ** 0.5)) ** 2, 1e-6)
    g.idio_var = idio


def rebalance(context, data):
    alpha = dict(g.alpha or {})
    if len(alpha) < 10:
        return

    symbols = list(alpha.keys())
    as_of = str(context.current_dt.date())
    n = len(symbols)
    equal = {sym: 1.0 / n for sym in symbols}
    bench_source = "equal_weight"
    try:
        w_bench, bench_source = get_csi300_bench_weights_with_meta(as_of, symbols=symbols)
        if not w_bench:
            w_bench = dict(equal)
            bench_source = "equal_weight"
    except Exception as exc:
        log("bench weights fallback: %s" % exc)
        w_bench = dict(equal)
        bench_source = "equal_weight"

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

    size_z = _zscore(log_mcap) if isinstance(log_mcap, pd.Series) and len(log_mcap) > 0 else pd.Series(dtype=float)
    size_z_map = {str(k): float(v) for k, v in size_z.items()} if len(size_z) else None
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
        industry=industry if industry else None,
        industry_limit=industry_limit,
        size_z=size_z_map,
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
    record_enhanced_index_diagnostics(
        as_of=as_of,
        weights=g.last_weights,
        w_bench=w_bench,
        optimize_result=result,
        bench_source=bench_source,
        industry=industry,
        size_z=size_z_map,
        kind="weekly",
    )
    log(
        "ei2 rebalance names=%d turnover=%.4f te=%.4f status=%s bench_source=%s industry=%d size=%d"
        % (
            len(g.last_weights),
            result.get("turnover") or 0.0,
            result.get("active_risk_proxy") or 0.0,
            result.get("status") or "",
            bench_source,
            len(industry or {}),
            len(size_z_map or {}),
        )
    )
