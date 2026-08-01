"""External Alpha Score Weekly
Long-only weekly Top-N on CSI300 using imported external alpha scores (PIT).

Reads scores with a configurable calendar-day lag, selects Top-N equal-weight names,
and flattens holdings outside the target set.
"""

# @param source str external External alpha signal source id range=
# @param version str default Score version tag range=
# @param top_n int 30 Number of holdings range=5:100:1
# @param min_names int 10 Minimum valid scores to rebalance range=3:50:1
# @param score_lag_days int 1 Calendar days lag from rebalance date to score as_of range=0:5:1

from datetime import timedelta

import pandas as pd


def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(5)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    source = str(context.params.get("source", "external"))
    version = str(context.params.get("version", "default"))
    top_n = int(context.params.get("top_n", 30))
    min_names = int(context.params.get("min_names", 10))
    score_lag_days = int(context.params.get("score_lag_days", 1))

    as_of = context.current_dt.date() - timedelta(days=score_lag_days)
    scores = get_external_alpha_scores(as_of, source, version=version)
    if scores is None:
        log("skip rebalance: no scores returned as_of=%s source=%s version=%s" % (as_of, source, version))
        return

    scores = pd.to_numeric(scores, errors="coerce").dropna()
    if len(scores) < min_names:
        log(
            "skip rebalance: valid_scores=%d < min_names=%d as_of=%s source=%s"
            % (len(scores), min_names, as_of, source)
        )
        return

    universe = get_universe_stocks()
    if universe:
        universe_set = set(universe)
        scores = scores[scores.index.isin(universe_set)]
        if len(scores) < min_names:
            log(
                "skip rebalance: universe_scores=%d < min_names=%d as_of=%s"
                % (len(scores), min_names, as_of)
            )
            return

    selected = list(scores.nlargest(top_n).index)
    if not selected:
        return

    target_weight = 1.0 / len(selected)
    current = get_positions()

    for symbol in current:
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="ext_alpha_exit")

    for symbol in selected:
        order_target_percent(symbol, target_weight, reason="ext_alpha_target")

    log("ext_alpha rebalance names=%d as_of=%s source=%s version=%s" % (len(selected), as_of, source, version))
