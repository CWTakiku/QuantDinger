-- ===== Strategy API V2 canonical template seed =====

DELETE FROM qd_script_templates
WHERE template_key NOT IN (
    'strategy_v2_single_ma',
    'strategy_v2_double_ma',
    'strategy_v2_bullish_three_lines',
    'strategy_v2_bullish_three_lines_trend',
    'strategy_v2_turtle',
    'strategy_v2_indicator_resonance',
    'strategy_v2_macd_kdj',
    'strategy_v2_supertrend',
    'strategy_v2_csi300_enhanced',
    'strategy_v2_csi300_enhanced_v2',
    'strategy_v2_market_cap_barbell',
    'strategy_v2_momentum_top_n',
    'strategy_v2_low_volatility',
    'strategy_v2_quality_growth'
);

INSERT INTO qd_script_templates
(template_key, asset_type, title, description, code, param_schema, tags, icon, accent, sort_order, is_active, metadata, updated_at)
VALUES
('strategy_v2_single_ma', 'script', 'Single Moving Average', 'A parameterized SPY trend strategy using one moving average.', $single$"""
Single Moving Average
SPY trend regime driven by a configurable moving average.
"""

# @param ma_period int 50 range=2:250:1
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "USStock:SPY"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(260)


def handle_data(context, data):
    ma_period = int(context.params.get("ma_period", 50))
    target_pct = float(context.params.get("target_pct", 0.95))
    bars = get_history(ma_period + 2, "1d", "close", g.symbol)
    if len(bars) < ma_period + 1:
        return
    close = bars["close"]
    average = float(close.iloc[:-1].tail(ma_period).mean())
    price = float(close.iloc[-1])
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if price > average and not is_long:
        order_target_percent(g.symbol, target_pct, reason="single_ma_entry")
    elif price <= average and is_long:
        order_target_percent(g.symbol, 0.0, reason="single_ma_exit")
$single$, '{"params":[{"name":"ma_period","type":"integer","default":50,"min":2,"max":250,"step":1,"labelKey":"strategyV2.params.maPeriod","descriptionKey":"strategyV2.params.maPeriodDesc"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition","descriptionKey":"strategyV2.params.targetPositionDesc"}]}'::jsonb, '["strategy-v2","cta","moving-average","us-stock"]'::jsonb, 'line-chart', 'green', 10, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_double_ma', 'script', 'Dual Moving Average', 'A parameterized BTC perpetual dual moving-average strategy with optional leverage.', $double$"""
Dual Moving Average
BTC perpetual trend strategy with configurable long and short regimes.
"""

# @param fast_period int 20 range=2:100:1
# @param slow_period int 60 range=5:300:1
# @param target_pct float 0.95 range=0.05:1:0.05
# @param allow_short bool true

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="4h")
    context.set_warmup(310)
    context.allow_leverage(max_leverage=20)


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 20))
    slow_period = int(context.params.get("slow_period", 60))
    target_pct = float(context.params.get("target_pct", 0.95))
    allow_short = bool(context.params.get("allow_short", True))
    if fast_period >= slow_period:
        return
    bars = get_history(slow_period + 2, "4h", "close", g.symbol)
    if len(bars) < slow_period + 1:
        return
    close = bars["close"]
    fast = float(close.tail(fast_period).mean())
    slow = float(close.tail(slow_period).mean())
    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)
    target = target_pct if fast > slow else (-target_pct if allow_short else 0.0)
    if (target > 0 and amount <= 0) or (target < 0 and amount >= 0) or (target == 0 and amount != 0):
        order_target_percent(g.symbol, target, reason="dual_ma_regime_change")
$double$, '{"params":[{"name":"fast_period","type":"integer","default":20,"min":2,"max":100,"step":1,"labelKey":"trading-assistant.templateParam.fast_period.label","descriptionKey":"trading-assistant.templateParam.fast_period.desc"},{"name":"slow_period","type":"integer","default":60,"min":5,"max":300,"step":1,"labelKey":"trading-assistant.templateParam.slow_period.label","descriptionKey":"trading-assistant.templateParam.slow_period.desc"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition","descriptionKey":"strategyV2.params.targetPositionDesc"},{"name":"allow_short","type":"boolean","default":true,"labelKey":"strategyV2.params.allowShort","descriptionKey":"strategyV2.params.allowShortDesc"}]}'::jsonb, '["strategy-v2","cta","moving-average","crypto","swap"]'::jsonb, 'swap', 'blue', 20, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_bullish_three_lines', 'script', 'Bullish Candle Through Three Averages', 'An A-share bullish candle breakout through three configurable averages.', $three$"""
Bullish Candle Through Three Averages
Daily A-share breakout through three configurable moving averages.
"""

# @param short_period int 5 range=2:60:1
# @param mid_period int 10 range=3:120:1
# @param long_period int 20 range=5:250:1
# @param min_body_pct float 0.02 range=0:0.2:0.005
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "CNStock:600519.SH"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(260)


def handle_data(context, data):
    periods = [
        int(context.params.get("short_period", 5)),
        int(context.params.get("mid_period", 10)),
        int(context.params.get("long_period", 20)),
    ]
    min_body_pct = float(context.params.get("min_body_pct", 0.02))
    target_pct = float(context.params.get("target_pct", 0.95))
    if not periods[0] < periods[1] < periods[2]:
        return
    bars = get_history(periods[-1] + 3, "1d", ["open", "close"], g.symbol)
    if len(bars) < periods[-1] + 1:
        return
    close = bars["close"]
    current = bars.iloc[-1]
    averages = [float(close.iloc[:-1].tail(period).mean()) for period in periods]
    open_price = float(current["open"])
    close_price = float(current["close"])
    body_pct = (close_price - open_price) / open_price if open_price > 0 else 0.0
    crossed = body_pct >= min_body_pct and open_price <= min(averages) and close_price >= max(averages)
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if crossed and not is_long:
        order_target_percent(g.symbol, target_pct, reason="bullish_three_lines_entry")
    elif is_long and close_price < averages[-1]:
        order_target_percent(g.symbol, 0.0, reason="bullish_three_lines_exit")
$three$, '{"params":[{"name":"short_period","type":"integer","default":5,"min":2,"max":60,"step":1,"labelKey":"strategyV2.params.shortPeriod","descriptionKey":"strategyV2.params.shortPeriodDesc"},{"name":"mid_period","type":"integer","default":10,"min":3,"max":120,"step":1,"labelKey":"strategyV2.params.midPeriod","descriptionKey":"strategyV2.params.midPeriodDesc"},{"name":"long_period","type":"integer","default":20,"min":5,"max":250,"step":1,"labelKey":"strategyV2.params.longPeriod","descriptionKey":"strategyV2.params.longPeriodDesc"},{"name":"min_body_pct","type":"percent","default":0.02,"min":0,"max":0.2,"step":0.005,"labelKey":"strategyV2.params.minBodyPct","descriptionKey":"strategyV2.params.minBodyPctDesc"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition","descriptionKey":"strategyV2.params.targetPositionDesc"}]}'::jsonb, '["strategy-v2","cta","candlestick","a-share"]'::jsonb, 'rise', 'red', 30, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_bullish_three_lines_trend', 'script', 'Bullish Three Averages With Trend Filter', 'The three-average breakout combined with a configurable rising trend filter.', $threetrend$"""
Bullish Three Averages With Trend Filter
Daily A-share breakout with a configurable rising trend filter.
"""

# @param short_period int 5 range=2:60:1
# @param mid_period int 10 range=3:120:1
# @param long_period int 20 range=5:250:1
# @param trend_period int 60 range=20:300:1
# @param trend_slope_bars int 5 range=1:30:1
# @param min_body_pct float 0.02 range=0:0.2:0.005
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "CNStock:600519.SH"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(340)


def handle_data(context, data):
    periods = [int(context.params.get("short_period", 5)), int(context.params.get("mid_period", 10)), int(context.params.get("long_period", 20))]
    trend_period = int(context.params.get("trend_period", 60))
    slope_bars = int(context.params.get("trend_slope_bars", 5))
    min_body_pct = float(context.params.get("min_body_pct", 0.02))
    target_pct = float(context.params.get("target_pct", 0.95))
    required = max(periods[-1], trend_period) + slope_bars + 2
    bars = get_history(required, "1d", ["open", "close"], g.symbol)
    if len(bars) < required - 1 or not periods[0] < periods[1] < periods[2]:
        return
    close = bars["close"]
    current = bars.iloc[-1]
    averages = [float(close.iloc[:-1].tail(period).mean()) for period in periods]
    trend_now = float(close.iloc[:-1].tail(trend_period).mean())
    trend_before = float(close.iloc[:-1 - slope_bars].tail(trend_period).mean())
    open_price = float(current["open"])
    close_price = float(current["close"])
    body_pct = (close_price - open_price) / open_price if open_price > 0 else 0.0
    crossed = body_pct >= min_body_pct and open_price <= min(averages) and close_price >= max(averages)
    trend_ok = close_price > trend_now and trend_now > trend_before
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if crossed and trend_ok and not is_long:
        order_target_percent(g.symbol, target_pct, reason="bullish_three_lines_trend_entry")
    elif is_long and (close_price < averages[-1] or not trend_ok):
        order_target_percent(g.symbol, 0.0, reason="bullish_three_lines_trend_exit")
$threetrend$, '{"params":[{"name":"short_period","type":"integer","default":5,"min":2,"max":60,"step":1,"labelKey":"strategyV2.params.shortPeriod"},{"name":"mid_period","type":"integer","default":10,"min":3,"max":120,"step":1,"labelKey":"strategyV2.params.midPeriod"},{"name":"long_period","type":"integer","default":20,"min":5,"max":250,"step":1,"labelKey":"strategyV2.params.longPeriod"},{"name":"trend_period","type":"integer","default":60,"min":20,"max":300,"step":1,"labelKey":"strategyV2.params.trendPeriod"},{"name":"trend_slope_bars","type":"integer","default":5,"min":1,"max":30,"step":1,"labelKey":"strategyV2.params.trendSlopeBars"},{"name":"min_body_pct","type":"percent","default":0.02,"min":0,"max":0.2,"step":0.005,"labelKey":"strategyV2.params.minBodyPct"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition"}]}'::jsonb, '["strategy-v2","cta","candlestick","trend","a-share"]'::jsonb, 'area-chart', 'orange', 40, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_turtle', 'script', 'Turtle Trading', 'A configurable Donchian breakout, channel exit, and ATR stop strategy on SPY.', $turtle$"""
Turtle Trading
Configurable Donchian breakout, channel exit, and ATR risk stop.
"""

# @param entry_period int 20 range=5:120:1
# @param exit_period int 10 range=2:60:1
# @param atr_period int 14 range=2:100:1
# @param atr_stop_mult float 2 range=0.5:10:0.25
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "USStock:SPY"
    g.entry_price = None
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(140)


def handle_data(context, data):
    entry_period = int(context.params.get("entry_period", 20))
    exit_period = int(context.params.get("exit_period", 10))
    atr_period = int(context.params.get("atr_period", 14))
    atr_stop_mult = float(context.params.get("atr_stop_mult", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    required = max(entry_period, exit_period, atr_period) + 2
    bars = get_history(required, "1d", ["high", "low", "close"], g.symbol)
    atr = indicator("ATR", g.symbol, timeperiod=atr_period)
    if len(bars) < required - 1 or len(atr) < 2:
        return
    close = float(bars["close"].iloc[-1])
    entry_high = float(bars["high"].iloc[-entry_period - 1:-1].max())
    exit_low = float(bars["low"].iloc[-exit_period - 1:-1].min())
    atr_value = float(atr.iloc[-1])
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if not is_long and close > entry_high:
        g.entry_price = close
        order_target_percent(g.symbol, target_pct, reason="turtle_breakout")
    elif is_long:
        stop_price = float(g.entry_price or close) - atr_stop_mult * atr_value
        if close < exit_low or close < stop_price:
            order_target_percent(g.symbol, 0.0, reason="turtle_exit")
            g.entry_price = None
$turtle$, '{"params":[{"name":"entry_period","type":"integer","default":20,"min":5,"max":120,"step":1,"labelKey":"strategyV2.params.entryPeriod"},{"name":"exit_period","type":"integer","default":10,"min":2,"max":60,"step":1,"labelKey":"strategyV2.params.exitPeriod"},{"name":"atr_period","type":"integer","default":14,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.atrPeriod"},{"name":"atr_stop_mult","type":"number","default":2,"min":0.5,"max":10,"step":0.25,"labelKey":"strategyV2.params.atrStopMult"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition"}]}'::jsonb, '["strategy-v2","cta","breakout","turtle","us-stock"]'::jsonb, 'flag', 'cyan', 50, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_indicator_resonance', 'script', 'Indicator Resonance', 'A parameterized QQQ strategy requiring MACD, RSI, and ADX confirmation.', $resonance$"""
Indicator Resonance
MACD, RSI, and ADX confirm the same bullish regime.
"""

# @param fast_period int 12 range=2:100:1
# @param slow_period int 26 range=3:200:1
# @param signal_period int 9 range=2:100:1
# @param rsi_period int 14 range=2:100:1
# @param rsi_min float 50 range=0:100:1
# @param rsi_max float 75 range=0:100:1
# @param adx_period int 14 range=2:100:1
# @param adx_min float 20 range=0:100:1
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "USStock:QQQ"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(210)


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 12))
    slow_period = int(context.params.get("slow_period", 26))
    signal_period = int(context.params.get("signal_period", 9))
    rsi_period = int(context.params.get("rsi_period", 14))
    rsi_min = float(context.params.get("rsi_min", 50))
    rsi_max = float(context.params.get("rsi_max", 75))
    adx_period = int(context.params.get("adx_period", 14))
    adx_min = float(context.params.get("adx_min", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    macd = indicator("MACD", g.symbol, fastperiod=fast_period, slowperiod=slow_period, signalperiod=signal_period)
    rsi = indicator("RSI", g.symbol, timeperiod=rsi_period)
    adx = indicator("ADX", g.symbol, timeperiod=adx_period)
    if len(macd) < 2 or len(rsi) < 2 or len(adx) < 2:
        return
    histogram = float(macd["macdhist"].iloc[-1])
    rsi_value = float(rsi.iloc[-1])
    adx_value = float(adx.iloc[-1])
    if not all(value == value for value in (histogram, rsi_value, adx_value)):
        return
    bullish = histogram > 0 and rsi_min < rsi_value < rsi_max and adx_value > adx_min
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if bullish and not is_long:
        order_target_percent(g.symbol, target_pct, reason="indicator_resonance_entry")
    elif not bullish and is_long:
        order_target_percent(g.symbol, 0.0, reason="indicator_resonance_exit")
$resonance$, '{"params":[{"name":"fast_period","type":"integer","default":12,"min":2,"max":100,"step":1,"labelKey":"trading-assistant.templateParam.fast_period.label"},{"name":"slow_period","type":"integer","default":26,"min":3,"max":200,"step":1,"labelKey":"trading-assistant.templateParam.slow_period.label"},{"name":"signal_period","type":"integer","default":9,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.signalPeriod"},{"name":"rsi_period","type":"integer","default":14,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.rsiPeriod"},{"name":"rsi_min","type":"number","default":50,"min":0,"max":100,"step":1,"labelKey":"strategyV2.params.rsiMin"},{"name":"rsi_max","type":"number","default":75,"min":0,"max":100,"step":1,"labelKey":"strategyV2.params.rsiMax"},{"name":"adx_period","type":"integer","default":14,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.adxPeriod"},{"name":"adx_min","type":"number","default":20,"min":0,"max":100,"step":1,"labelKey":"strategyV2.params.adxMin"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition"}]}'::jsonb, '["strategy-v2","cta","ta-lib","resonance","us-stock"]'::jsonb, 'fund', 'purple', 60, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_macd_kdj', 'script', 'MACD and KDJ Confirmation', 'A BTC perpetual strategy combining MACD momentum, stochastic KDJ confirmation, and explicit position protection.', $macdkdj$"""
MACD and KDJ Confirmation
BTC perpetual momentum with state-transition entries confirmed by MACD and stochastic KDJ.
"""

# @param fast_period int 12 range=2:100:1
# @param slow_period int 26 range=3:200:1
# @param signal_period int 9 range=2:100:1
# @param kdj_period int 9 range=2:100:1
# @param kdj_smooth_k int 3 range=1:20:1
# @param kdj_smooth_d int 3 range=1:20:1
# @param overbought float 85 range=50:100:1
# @param target_pct float 0.95 range=0.1:5:0.05
# @param stop_loss_pct float 0.02 range=0.005:0.2:0.005
# @param trailing_activation_pct float 0.05 range=0.005:0.5:0.005
# @param trailing_stop_pct float 0.01 range=0.005:0.2:0.005

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="4h")
    context.set_warmup(210)
    context.allow_leverage(max_leverage=5)


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 12))
    slow_period = int(context.params.get("slow_period", 26))
    signal_period = int(context.params.get("signal_period", 9))
    kdj_period = int(context.params.get("kdj_period", 9))
    kdj_smooth_k = int(context.params.get("kdj_smooth_k", 3))
    kdj_smooth_d = int(context.params.get("kdj_smooth_d", 3))
    overbought = float(context.params.get("overbought", 85))
    target_pct = float(context.params.get("target_pct", 0.95))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.02))
    trailing_activation_pct = float(context.params.get("trailing_activation_pct", 0.05))
    trailing_stop_pct = float(context.params.get("trailing_stop_pct", 0.01))
    macd = indicator("MACD", g.symbol, fastperiod=fast_period, slowperiod=slow_period, signalperiod=signal_period)
    kdj = indicator("STOCH", g.symbol, fastk_period=kdj_period, slowk_period=kdj_smooth_k, slowd_period=kdj_smooth_d)
    if len(macd) < 2 or len(kdj) < 2:
        return
    previous_histogram = float(macd["macdhist"].iloc[-2])
    histogram = float(macd["macdhist"].iloc[-1])
    previous_k = float(kdj["slowk"].iloc[-2])
    previous_d = float(kdj["slowd"].iloc[-2])
    k_value = float(kdj["slowk"].iloc[-1])
    d_value = float(kdj["slowd"].iloc[-1])
    if not all(value == value for value in (previous_histogram, histogram, previous_k, previous_d, k_value, d_value)):
        return
    macd_cross_up = previous_histogram <= 0 < histogram
    kdj_cross_up = previous_k <= previous_d and k_value > d_value
    enter = histogram > 0 and (macd_cross_up or kdj_cross_up) and k_value < overbought
    exit_signal = histogram <= 0 or (previous_k >= previous_d and k_value < d_value)
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if enter and not is_long:
        order_target_percent(
            g.symbol,
            target_pct,
            reason="macd_kdj_entry",
            stop_loss_pct=stop_loss_pct,
            trailing_activation_pct=trailing_activation_pct,
            trailing_stop_pct=trailing_stop_pct,
        )
    elif exit_signal and is_long:
        order_target_percent(g.symbol, 0.0, reason="macd_kdj_exit")
$macdkdj$, '{"params":[{"name":"fast_period","type":"integer","default":12,"min":2,"max":100,"step":1,"labelKey":"trading-assistant.templateParam.fast_period.label"},{"name":"slow_period","type":"integer","default":26,"min":3,"max":200,"step":1,"labelKey":"trading-assistant.templateParam.slow_period.label"},{"name":"signal_period","type":"integer","default":9,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.signalPeriod"},{"name":"kdj_period","type":"integer","default":9,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.kdjPeriod"},{"name":"kdj_smooth_k","type":"integer","default":3,"min":1,"max":20,"step":1,"labelKey":"strategyV2.params.kdjSmoothK"},{"name":"kdj_smooth_d","type":"integer","default":3,"min":1,"max":20,"step":1,"labelKey":"strategyV2.params.kdjSmoothD"},{"name":"overbought","type":"number","default":85,"min":50,"max":100,"step":1,"labelKey":"trading-assistant.templateParam.overbought.label"},{"name":"target_pct","type":"number","default":0.95,"min":0.1,"max":5,"step":0.05,"labelKey":"strategyV2.params.targetExposure"},{"name":"stop_loss_pct","type":"percent","default":0.02,"min":0.005,"max":0.2,"step":0.005,"labelKey":"strategyV2.params.stopLoss"},{"name":"trailing_activation_pct","type":"percent","default":0.05,"min":0.005,"max":0.5,"step":0.005,"labelKey":"strategyV2.params.trailingActivation"},{"name":"trailing_stop_pct","type":"percent","default":0.01,"min":0.005,"max":0.2,"step":0.005,"labelKey":"strategyV2.params.trailingDrawdown"}]}'::jsonb, '["strategy-v2","cta","ta-lib","macd","kdj","crypto","swap","risk"]'::jsonb, 'bar-chart', 'gold', 70, TRUE, '{"source":"system_seed","version":10,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_supertrend', 'script', 'SuperTrend', 'A configurable SPY SuperTrend strategy using ATR trailing bands.', $supertrend$"""
SuperTrend
ATR trailing bands define a stateful SPY trend regime.
"""

# @param atr_period int 10 range=2:100:1
# @param atr_multiplier float 3 range=0.5:10:0.25
# @param target_pct float 0.95 range=0.05:1:0.05

def initialize(context):
    g.symbol = "USStock:SPY"
    g.trend = 0
    g.upper_band = None
    g.lower_band = None
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1d")
    context.set_warmup(120)


def handle_data(context, data):
    atr_period = int(context.params.get("atr_period", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    bars = get_history(atr_period + 3, "1d", ["high", "low", "close"], g.symbol)
    atr = indicator("ATR", g.symbol, timeperiod=atr_period)
    if len(bars) < atr_period + 2 or len(atr) < 2:
        return
    high = float(bars["high"].iloc[-1])
    low = float(bars["low"].iloc[-1])
    close = float(bars["close"].iloc[-1])
    previous_close = float(bars["close"].iloc[-2])
    atr_value = float(atr.iloc[-1])
    middle = (high + low) / 2.0
    basic_upper = middle + multiplier * atr_value
    basic_lower = middle - multiplier * atr_value
    previous_upper = float(g.upper_band) if g.upper_band is not None else basic_upper
    previous_lower = float(g.lower_band) if g.lower_band is not None else basic_lower
    g.upper_band = basic_upper if basic_upper < previous_upper or previous_close > previous_upper else previous_upper
    g.lower_band = basic_lower if basic_lower > previous_lower or previous_close < previous_lower else previous_lower
    if close > previous_upper:
        g.trend = 1
    elif close < previous_lower:
        g.trend = -1
    position = get_position(g.symbol)
    is_long = float(position.amount or 0.0) > 0
    if g.trend > 0 and not is_long:
        order_target_percent(g.symbol, target_pct, reason="supertrend_entry")
    elif g.trend < 0 and is_long:
        order_target_percent(g.symbol, 0.0, reason="supertrend_exit")
$supertrend$, '{"params":[{"name":"atr_period","type":"integer","default":10,"min":2,"max":100,"step":1,"labelKey":"strategyV2.params.atrPeriod"},{"name":"atr_multiplier","type":"number","default":3,"min":0.5,"max":10,"step":0.25,"labelKey":"strategyV2.params.atrMultiplier"},{"name":"target_pct","type":"percent","default":0.95,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.targetPosition"}]}'::jsonb, '["strategy-v2","cta","supertrend","atr","us-stock"]'::jsonb, 'stock', 'lime', 80, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_market_cap_barbell', 'portfolio_strategy', 'Small and Large Cap Barbell', 'A weekly cross-sectional portfolio combining small and large eligible U.S. companies.', $marketcap$"""
Small and Large Cap Barbell
Weekly point-in-time market-cap barbell with a profitability filter.
"""

# @param per_side int 3 range=1:6:1
# @param min_roe float 0 range=-1:1:0.01
# @param max_weight float 0.2 range=0.05:1:0.05

def initialize(context):
    g.universe = [
        "USStock:AAPL", "USStock:MSFT", "USStock:NVDA", "USStock:AMZN", "USStock:META",
        "USStock:GOOGL", "USStock:AVGO", "USStock:COST", "USStock:JPM", "USStock:XOM",
    ]
    context.set_universe(g.universe)
    context.set_benchmark("USStock:SPY")
    context.subscribe(frequency="1d")
    context.set_warmup(10)
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    per_side = int(context.params.get("per_side", 3))
    min_roe = float(context.params.get("min_roe", 0.0))
    max_weight = float(context.params.get("max_weight", 0.2))
    symbols = list(g.universe)
    fundamentals = get_fundamentals(["MARKET_CAP", "ROE"], symbols)
    if fundamentals.empty:
        return
    eligible = fundamentals.dropna(subset=["MARKET_CAP"])
    if "ROE" in eligible.columns:
        eligible = eligible[(eligible["ROE"].isna()) | (eligible["ROE"] >= min_roe)]
    ranking = eligible.sort_values("MARKET_CAP")
    selected = list(dict.fromkeys(list(ranking.head(per_side).index) + list(ranking.tail(per_side).index)))
    for symbol in get_positions().keys():
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="market_cap_removed")
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    for symbol in selected:
        order_target_percent(symbol, weight, reason="market_cap_barbell")
$marketcap$, '{"params":[{"name":"per_side","type":"integer","default":3,"min":1,"max":6,"step":1,"labelKey":"strategyV2.params.perSide"},{"name":"min_roe","type":"number","default":0,"min":-1,"max":1,"step":0.01,"labelKey":"strategyV2.params.minRoe"},{"name":"max_weight","type":"percent","default":0.2,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.maxWeight"}]}'::jsonb, '["strategy-v2","portfolio","cross-sectional","fundamental","market-cap"]'::jsonb, 'appstore', 'geekblue', 110, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_momentum_top_n', 'portfolio_strategy', 'Momentum Top-N Rotation', 'Weekly CSI500 (中证500) long-only Top-N by trailing price momentum.', $momentum$"""
Momentum Top-N Rotation
Weekly CSI500 long-only rotation into the strongest trailing momentum names.
"""

# @param lookback int 60 Momentum lookback days range=10:250:5
# @param top_n int 10 Number of holdings range=1:30:1
# @param max_weight float 0.12 Max weight per name range=0.05:1:0.05

def initialize(context):
    context.set_universe(pool="csi500")
    context.set_benchmark("CNStock:000905.SH")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(130)
    context.set_metadata(direction_mode="long_only")
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    lookback = int(context.params.get("lookback", 60))
    top_n = int(context.params.get("top_n", 10))
    max_weight = float(context.params.get("max_weight", 0.12))
    scores = {}
    for symbol in get_universe_stocks():
        try:
            bars = get_history(lookback + 1, "1d", "close", symbol)
        except Exception:
            continue
        if bars is None or len(bars) < lookback + 1:
            continue
        first = float(bars["close"].iloc[0])
        last = float(bars["close"].iloc[-1])
        if first > 0:
            scores[symbol] = last / first - 1.0
    selected = [
        symbol
        for symbol, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_n]
        if score > 0
    ]
    for symbol in get_positions().keys():
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="momentum_removed")
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    for symbol in selected:
        order_target_percent(symbol, weight, reason="momentum_top_n")
$momentum$, '{"params":[{"name":"lookback","type":"integer","default":60,"min":10,"max":250,"step":5,"labelKey":"strategyV2.params.lookback"},{"name":"top_n","type":"integer","default":10,"min":1,"max":30,"step":1,"labelKey":"strategyV2.params.topN"},{"name":"max_weight","type":"percent","default":0.12,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.maxWeight"}]}'::jsonb, '["strategy-v2","portfolio","cross-sectional","momentum","rotation","csi500","cn-stock"]'::jsonb, 'rocket', 'blue', 120, TRUE, '{"source":"system_seed","version":9,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_low_volatility', 'portfolio_strategy', 'Low Volatility Rotation', 'A weekly U.S. stock portfolio selecting the lowest realized volatility names.', $lowvol$"""
Low Volatility Rotation
Weekly cross-sectional rotation into the lowest realized volatility names.
"""

# @param lookback int 60 range=10:250:5
# @param top_n int 4 range=1:10:1
# @param max_weight float 0.25 range=0.05:1:0.05

def initialize(context):
    g.universe = [
        "USStock:AAPL", "USStock:MSFT", "USStock:NVDA", "USStock:AMZN", "USStock:META",
        "USStock:GOOGL", "USStock:AVGO", "USStock:COST", "USStock:JPM", "USStock:XOM",
    ]
    context.set_universe(g.universe)
    context.set_benchmark("USStock:SPY")
    context.subscribe(frequency="1d")
    context.set_warmup(260)
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    lookback = int(context.params.get("lookback", 60))
    top_n = int(context.params.get("top_n", 4))
    max_weight = float(context.params.get("max_weight", 0.25))
    scores = {}
    for symbol in g.universe:
        bars = get_history(lookback + 1, "1d", "close", symbol)
        if len(bars) < lookback + 1:
            continue
        returns = bars["close"].pct_change().dropna()
        if len(returns):
            scores[symbol] = float(returns.std())
    selected = [symbol for symbol, _ in sorted(scores.items(), key=lambda item: item[1])[:top_n]]
    for symbol in get_positions().keys():
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="low_vol_removed")
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    for symbol in selected:
        order_target_percent(symbol, weight, reason="low_volatility")
$lowvol$, '{"params":[{"name":"lookback","type":"integer","default":60,"min":10,"max":250,"step":5,"labelKey":"strategyV2.params.lookback"},{"name":"top_n","type":"integer","default":4,"min":1,"max":10,"step":1,"labelKey":"strategyV2.params.topN"},{"name":"max_weight","type":"percent","default":0.25,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.maxWeight"}]}'::jsonb, '["strategy-v2","portfolio","cross-sectional","low-volatility","rotation"]'::jsonb, 'safety', 'cyan', 130, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_quality_growth', 'portfolio_strategy', 'Quality Growth Multi-Factor', 'A weekly point-in-time portfolio combining profitability, growth, and balance-sheet quality.', $quality$"""
Quality Growth Multi-Factor
Weekly point-in-time ranking by profitability, growth, and balance-sheet quality.
"""

# @param top_n int 5 range=1:10:1
# @param min_roe float 0.1 range=-1:1:0.01
# @param min_growth float 0 range=-1:5:0.01
# @param max_debt_to_equity float 2 range=0:10:0.1
# @param max_weight float 0.2 range=0.05:1:0.05

def initialize(context):
    g.universe = [
        "USStock:AAPL", "USStock:MSFT", "USStock:NVDA", "USStock:AMZN", "USStock:META",
        "USStock:GOOGL", "USStock:AVGO", "USStock:COST", "USStock:JPM", "USStock:XOM",
    ]
    context.set_universe(g.universe)
    context.set_benchmark("USStock:SPY")
    context.subscribe(frequency="1d")
    context.set_warmup(10)
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    top_n = int(context.params.get("top_n", 5))
    min_roe = float(context.params.get("min_roe", 0.1))
    min_growth = float(context.params.get("min_growth", 0.0))
    max_debt = float(context.params.get("max_debt_to_equity", 2.0))
    max_weight = float(context.params.get("max_weight", 0.2))
    symbols = list(g.universe)
    factors = get_fundamentals(["ROE", "REVENUE_GROWTH", "DEBT_TO_EQUITY"], symbols)
    if factors.empty:
        return
    eligible = factors.dropna(subset=["ROE", "REVENUE_GROWTH", "DEBT_TO_EQUITY"])
    eligible = eligible[(eligible["ROE"] >= min_roe) & (eligible["REVENUE_GROWTH"] >= min_growth) & (eligible["DEBT_TO_EQUITY"] <= max_debt)]
    if eligible.empty:
        selected = []
    else:
        score = eligible["ROE"].rank(pct=True) + eligible["REVENUE_GROWTH"].rank(pct=True) - eligible["DEBT_TO_EQUITY"].rank(pct=True)
        selected = list(score.sort_values(ascending=False).head(top_n).index)
    for symbol in get_positions().keys():
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="quality_removed")
    weight = min(max_weight, 1.0 / len(selected)) if selected else 0.0
    for symbol in selected:
        order_target_percent(symbol, weight, reason="quality_growth")
$quality$, '{"params":[{"name":"top_n","type":"integer","default":5,"min":1,"max":10,"step":1,"labelKey":"strategyV2.params.topN"},{"name":"min_roe","type":"number","default":0.1,"min":-1,"max":1,"step":0.01,"labelKey":"strategyV2.params.minRoe"},{"name":"min_growth","type":"number","default":0,"min":-1,"max":5,"step":0.01,"labelKey":"strategyV2.params.minGrowth"},{"name":"max_debt_to_equity","type":"number","default":2,"min":0,"max":10,"step":0.1,"labelKey":"strategyV2.params.maxDebtToEquity"},{"name":"max_weight","type":"percent","default":0.2,"min":0.05,"max":1,"step":0.05,"labelKey":"strategyV2.params.maxWeight"}]}'::jsonb, '["strategy-v2","portfolio","cross-sectional","fundamental","quality","growth"]'::jsonb, 'radar-chart', 'purple', 140, TRUE, '{"source":"system_seed","version":8,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_csi300_enhanced', 'portfolio_strategy', 'CSI300 Enhanced Index QP', 'Weekly CSI300 enhanced-index portfolio: momentum/vol alpha with diagonal-risk QP rebalance.', $csehen$"""
CSI300 Enhanced Index (Weekly QP)
Long-only CSI300 enhanced index: daily alpha from momentum/vol, weekly QP rebalance.

Institutional universe rule: names shorter than min_history_bars stay out of the
alpha / QP set (no fabricated pre-IPO bars).
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
$csehen$, '{"params":[{"name":"universe_top_n","type":"integer","default":50,"min":20,"max":300,"step":10,"labelKey":"strategyV2.params.universeTopN"},{"name":"active_limit","type":"number","default":0.025,"min":0.01,"max":0.05,"step":0.005,"labelKey":"strategyV2.params.activeLimit"},{"name":"risk_aversion","type":"number","default":1.0,"min":0.1,"max":5.0,"step":0.1,"labelKey":"strategyV2.params.riskAversion"},{"name":"turn_penalty","type":"number","default":0.01,"min":0.0,"max":0.1,"step":0.005,"labelKey":"strategyV2.params.turnPenalty"},{"name":"min_turnover","type":"number","default":0.02,"min":0.0,"max":0.1,"step":0.01,"labelKey":"strategyV2.params.minTurnover"},{"name":"mom_fast","type":"integer","default":60,"min":20,"max":120,"step":5,"labelKey":"strategyV2.params.momFast"},{"name":"mom_slow","type":"integer","default":120,"min":60,"max":250,"step":10,"labelKey":"strategyV2.params.momSlow"},{"name":"vol_period","type":"integer","default":20,"min":10,"max":60,"step":5,"labelKey":"strategyV2.params.volPeriod"},{"name":"min_history_bars","type":"integer","default":0,"min":0,"max":260,"step":1,"labelKey":"strategyV2.params.minHistoryBars"}]}'::jsonb, '["strategy-v2","portfolio","csi300","cn-stock","enhanced-index","qp"]'::jsonb, 'fund', 'orange', 100, TRUE, '{"source":"system_seed","version":10,"apiVersion":2}'::jsonb, NOW()),

('strategy_v2_csi300_enhanced_v2', 'portfolio_strategy', 'CSI300 Enhanced Index QP 2.0', 'Weekly CSI300 enhanced-index 2.0: layered neutralized alpha with industry/size/TE QP.', $csehv$"""CSI300 Enhanced Index QP 2.0
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
        frame = panel.get(name)
        if frame is None or not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame()
        frame.loc[dt] = series
        frame = frame.sort_index().tail(max_rows)
        panel[name] = frame
    return panel


def _forward_return_panel(symbols, as_of, lookback=80):
    """One-day forward return panel aligned to layer history dates."""
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
        out = out.loc[: pd.Timestamp(as_of)]
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
        fwd = _forward_return_panel(list(layer_scores.get("momentum", pd.Series(dtype=float)).index or []), as_of)
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
$csehv$, '{"params":[{"name":"universe_top_n","type":"integer","default":50,"min":20,"max":300,"step":10,"labelKey":"strategyV2.params.universeTopN"},{"name":"active_limit","type":"number","default":0.025,"min":0.01,"max":0.05,"step":0.005,"labelKey":"strategyV2.params.activeLimit"},{"name":"risk_aversion","type":"number","default":1.0,"min":0.1,"max":5.0,"step":0.1,"labelKey":"strategyV2.params.riskAversion"},{"name":"turn_penalty","type":"number","default":0.01,"min":0.0,"max":0.1,"step":0.005,"labelKey":"strategyV2.params.turnPenalty"},{"name":"min_turnover","type":"number","default":0.02,"min":0.0,"max":0.1,"step":0.01,"labelKey":"strategyV2.params.minTurnover"},{"name":"mom_fast","type":"integer","default":60,"min":20,"max":120,"step":5,"labelKey":"strategyV2.params.momFast"},{"name":"mom_slow","type":"integer","default":120,"min":60,"max":250,"step":10,"labelKey":"strategyV2.params.momSlow"},{"name":"vol_period","type":"integer","default":20,"min":10,"max":60,"step":5,"labelKey":"strategyV2.params.volPeriod"},{"name":"min_history_bars","type":"integer","default":0,"min":0,"max":260,"step":1,"labelKey":"strategyV2.params.minHistoryBars"},{"name":"industry_limit","type":"number","default":0.05,"min":0.01,"max":0.15,"step":0.005,"labelKey":"strategyV2.params.industryLimit"},{"name":"size_limit","type":"number","default":0.3,"min":0.05,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.sizeLimit"},{"name":"te_limit","type":"number","default":0.08,"min":0.01,"max":0.25,"step":0.01,"labelKey":"strategyV2.params.teLimit"},{"name":"w_momentum","type":"number","default":0.35,"min":0.0,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.wMomentum"},{"name":"w_risk_liq","type":"number","default":0.2,"min":0.0,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.wRiskLiq"},{"name":"w_value_quality","type":"number","default":0.2,"min":0.0,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.wValueQuality"},{"name":"w_flow","type":"number","default":0.15,"min":0.0,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.wFlow"},{"name":"w_consensus","type":"number","default":0.1,"min":0.0,"max":1.0,"step":0.05,"labelKey":"strategyV2.params.wConsensus"}]}'::jsonb, '["strategy-v2","portfolio","csi300","cn-stock","enhanced-index","qp","v2"]'::jsonb, 'fund', 'orange', 101, TRUE, '{"source":"system_seed","version":10,"apiVersion":2}'::jsonb, NOW())
ON CONFLICT (template_key) DO UPDATE SET
    asset_type = EXCLUDED.asset_type,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    code = EXCLUDED.code,
    param_schema = EXCLUDED.param_schema,
    tags = EXCLUDED.tags,
    icon = EXCLUDED.icon,
    accent = EXCLUDED.accent,
    sort_order = EXCLUDED.sort_order,
    is_active = TRUE,
    metadata = EXCLUDED.metadata,
    updated_at = NOW();
