"""CSI300 Enhanced Index (Weekly QP)
Long-only CSI300 enhanced index: daily alpha from momentum/vol, weekly QP rebalance.

MVP: equal-weight benchmark proxy + diagonal-risk optimize_enhanced_index.
Institutional universe rule: names shorter than min_history_bars stay out of the
alpha / QP set (no fabricated pre-IPO bars). Full index weights / industry /
northbound factors come in later sync tasks.
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
    # Single-symbol path returns one DataFrame.
    if len(symbols) == 1 and len(hist) >= min_bars:
        return [symbols[0]]
    return []


def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    # Cover default mom_slow=120 (+ buffer). Raise if you use a longer lookback.
    context.set_warmup(140)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    g.alpha = {}
    g.last_weights = {}
    run_daily(update_alpha, time="15:05")
    run_weekly(rebalance, weekday=1, time="09:35")


def update_alpha(context, data):
    symbols = get_universe_stocks()
    if not symbols:
        return
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

    frame = pd.DataFrame({
        "mom_fast": fast["momentum"] if "momentum" in fast.columns else pd.Series(dtype=float),
        "mom_slow": slow["momentum"] if "momentum" in slow.columns else pd.Series(dtype=float),
        "vol": vol["realized_volatility"] if "realized_volatility" in vol.columns else pd.Series(dtype=float),
    }).dropna(how="any")
    if len(frame) < 10:
        return

    pieces = []
    for col, sign in (("mom_fast", 1.0), ("mom_slow", 1.0), ("vol", -1.0)):
        series = frame[col].astype(float)
        med = float(series.median())
        mad = float((series - med).abs().median())
        if mad > 0:
            radius = 5.0 * 1.4826 * mad
            series = series.clip(med - radius, med + radius)
        std = float(series.std(ddof=0))
        if std <= 0:
            continue
        z = (series - float(series.mean())) / std
        pieces.append(z * sign)
    if not pieces:
        return
    alpha = pd.concat(pieces, axis=1).mean(axis=1)
    std = float(alpha.std(ddof=0))
    if std > 0:
        alpha = (alpha - float(alpha.mean())) / std
    g.alpha = {str(k): float(v) for k, v in alpha.items()}


def rebalance(context, data):
    alpha = dict(g.alpha or {})
    if len(alpha) < 10:
        return

    symbols = list(alpha.keys())
    n = len(symbols)
    w_bench = {sym: 1.0 / n for sym in symbols}
    w_prev = dict(g.last_weights) if g.last_weights else dict(w_bench)

    result = optimize_enhanced_index(
        alpha,
        w_bench,
        w_prev=w_prev,
        active_limit=float(context.params.get("active_limit", 0.025)),
        risk_aversion=float(context.params.get("risk_aversion", 1.0)),
        turn_penalty=float(context.params.get("turn_penalty", 0.01)),
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
        "ei rebalance names=%d turnover=%.4f te_proxy=%.4f"
        % (len(g.last_weights), result.get("turnover") or 0.0, result.get("active_risk_proxy") or 0.0)
    )
