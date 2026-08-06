"""中国巨石卫星仓 CTA
Long-only satellite sleeve on CNStock:600176.SH with industry-week gate.
Signal-mode friendly. Core portfolio stays outside this strategy ledger.

Intraday T (enable_intraday_t): MVP does not submit separate 14:55 flatten orders.
When armed, the strategy only logs that intraday T is allowed; an external executor
must flatten any T sleeve before the close. Daily order_target_percent remains the
satellite sleeve target (not a T round-trip).
"""

# @param satellite_max_pct float 0.20 Max satellite sleeve weight range=0.05:0.20:0.01
# @param hard_stop_pct float 0.12 Hard stop on satellite sleeve range=0.05:0.25:0.01
# @param t_max_frac float 0.25 Intraday T max fraction of sleeve range=0.05:0.25:0.01
# @param min_amount float 4000000000 Min avg daily turnover CNY range=1000000000:10000000000:100000000
# @param liquidity_lookback int 5 Lookback days for liquidity gate range=3:20:1
# @param enable_intraday_t int 1 Log intraday T arm signal range=0:1:1
# @param market_stress int 0 Force flat when set range=0:1:1
# @param cash_profit_ratio float 1.0 Net cash flow / profit ratio range=0:2:0.05
# @param gm_qoq float 0.0 Gross margin QoQ change range=-0.5:0.5:0.01
# @param pe_percentile float -1 Manual PE percentile 0-1; -1 uses Tushare pe_ttm rolling pct range=-1:1:0.01
# @param pe_exit_percentile float 0.90 Exit when PE percentile at or above range=0.5:1:0.01
# @param pe_lookback_days int 756 Lookback calendar days for PE percentile range=120:1500:1


def _industry_downturn(industry):
    if not industry or not industry.get("industry_available"):
        return False
    return int(industry["cloth_trend"]) < 0 and int(industry["inventory_trend"]) > 0


def _should_record_industry_week(last_industry, curr_as_of):
    if curr_as_of is None:
        return False
    if last_industry is None:
        return True
    return last_industry.get("as_of") != curr_as_of


def _is_two_week_downturn(last_industry, current):
    # Consecutive weeks = two distinct resolved industry as_of values, not calendar adjacency.
    if not last_industry or not current:
        return False
    last_as_of = last_industry.get("as_of")
    curr_as_of = current.get("as_of")
    if last_as_of is None or curr_as_of is None or last_as_of == curr_as_of:
        return False
    return _industry_downturn(last_industry) and _industry_downturn(current)


def _snapshot_industry_week(industry):
    return {
        "industry_available": True,
        "cloth_trend": int(industry["cloth_trend"]),
        "inventory_trend": int(industry["inventory_trend"]),
        "new_capacity_flag": int(industry["new_capacity_flag"]),
        "as_of": industry.get("as_of"),
    }


def _maybe_record_industry_week(last_industry, industry):
    if not industry.get("industry_available"):
        return last_industry
    curr_as_of = industry.get("as_of")
    if not _should_record_industry_week(last_industry, curr_as_of):
        return last_industry
    return _snapshot_industry_week(industry)


def _avg_daily_amount(symbol, lookback):
    bars = get_history(lookback, "1d", ["close", "volume"], symbol)
    if bars is None or len(bars) < max(1, lookback // 2):
        return 0.0
    if "close" not in bars.columns or "volume" not in bars.columns:
        return 0.0
    amounts = bars["close"] * bars["volume"]
    return float(amounts.mean())


def _resolve_pe_percentile(params, as_of=None):
    # Manual override still wins. Otherwise use Tushare daily_basic pe_ttm
    # rolling percentile via get_ashare_pe_percentile (not get_fundamentals).
    manual = float(params.get("pe_percentile", -1))
    if manual >= 0:
        return manual
    try:
        snap = get_ashare_pe_percentile(g.symbol, as_of, int(params.get("pe_lookback_days", 756)))
        value = None if not snap else snap.get("pe_percentile")
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def initialize(context):
    g.symbol = "CNStock:600176.SH"
    g.last_industry = None
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(80)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    set_default_protection(stop_loss_pct=0.12)


def handle_data(context, data):
    industry = get_glass_fiber_industry_week(context.current_dt.date())
    params = context.params
    sat_max = float(params.get("satellite_max_pct", 0.20))
    hard_stop = float(params.get("hard_stop_pct", 0.12))
    market_stress = int(params.get("market_stress", 0))
    cash_ratio = float(params.get("cash_profit_ratio", 1.0))
    gm_qoq = float(params.get("gm_qoq", 0.0))
    min_amount = float(params.get("min_amount", 4000000000))
    liquidity_lookback = int(params.get("liquidity_lookback", 5))
    pe_exit = float(params.get("pe_exit_percentile", 0.90))

    if market_stress or (cash_ratio < 0.8 and gm_qoq < 0):
        order_target_percent(g.symbol, 0.0, reason="jushi_hard_exit_fund_or_stress")
        return

    pe_pct = _resolve_pe_percentile(params, context.current_dt.date())
    if pe_pct is not None and pe_pct >= pe_exit:
        order_target_percent(g.symbol, 0.0, reason="jushi_pe_exit")
        return

    if not industry.get("industry_available"):
        pos = get_position(g.symbol)
        if pos is None or abs(float(pos.amount or 0)) < 1e-9:
            return
        return

    if _is_two_week_downturn(g.last_industry, industry):
        order_target_percent(g.symbol, 0.0, reason="jushi_industry_downturn")
        g.last_industry = _maybe_record_industry_week(g.last_industry, industry)
        return

    allow = (
        int(industry["cloth_trend"]) >= 0
        and int(industry["inventory_trend"]) <= 0
        and int(industry["new_capacity_flag"]) == 0
    )
    target = sat_max if allow else 0.0

    avg_amount = _avg_daily_amount(g.symbol, liquidity_lookback)
    if avg_amount < min_amount:
        target = 0.0

    order_target_percent(
        g.symbol,
        target,
        reason="jushi_satellite_target",
        stop_loss_pct=hard_stop if target > 0 else 0.0,
    )

    if target > 0 and int(params.get("enable_intraday_t", 1)) == 1:
        log(
            "jushi intraday T armed frac<=%s (signal; execute externally)"
            % params.get("t_max_frac", 0.25)
        )

    g.last_industry = _maybe_record_industry_week(g.last_industry, industry)
