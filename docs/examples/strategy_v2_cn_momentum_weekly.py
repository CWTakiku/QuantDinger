"""CN CSI Momentum Weekly
Long-only weekly Top-N by 20-day momentum on CSI300 (default).

Signal-mode friendly: no short, rebalance only on weekly schedule.

For CSI500 (中证500), deploy a separate copy with:
  - context.set_universe(pool="csi500")
  - context.set_benchmark("CNStock:000905.SH")
"""

# @param holdings int 10 Number of holdings range=3:30:1
# @param momentum_period int 20 Momentum lookback days range=5:60:1
# @param max_weight float 0.12 Max weight per name range=0.05:0.25:0.01


def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(80)
    context.set_benchmark("CNStock:000300.SH")
    # weekday: 1=Monday
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    holdings = int(context.params.get("holdings", 10))
    period = int(context.params.get("momentum_period", 20))
    max_weight = float(context.params.get("max_weight", 0.12))
    symbols = get_universe_stocks()
    if len(symbols) < holdings:
        return

    scores = get_factors(symbols, "momentum", period=period)
    if scores is None or scores.empty or "momentum" not in scores.columns:
        return

    ranked = scores["momentum"].dropna().sort_values(ascending=False)
    selected = list(ranked.head(holdings).index)
    if not selected:
        return

    target_weight = min(max_weight, 0.95 / len(selected))
    current = get_positions()

    for symbol in current:
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="weekly_remove")

    for symbol in selected:
        order_target_percent(symbol, target_weight, reason="weekly_select")
