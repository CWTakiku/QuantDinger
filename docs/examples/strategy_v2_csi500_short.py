"""中证 500 短线。

每天 09:30 用已经收盘的日线做决定，当天开盘成交。信号用复权价。
股票池是中证 500，只做主板、创业板、科创板，排除北交所和 ST。

四种形态至少命中一种才开仓：放量突破、超跌反转、首板次日、强势板块。
大盘用股票池 5 日收益的中位数；跌破阈值就不再开新仓。
单票不超过 15%，目标 8 只、最多 10 只。从净值高点回撤达到 15% 后只出不进。
出场：止损、从持仓后高点回撤、跌破 5 日均线，或持有满 10 个交易日。

美股隔夜涨跌没有进日线库。只有参数 us_overnight_return 有值时才参与风控。
回测滑点用 0.2%。调参用 2021–2023，样本外用 2024–2026。
"""

# @param holdings int 8 Target names range=8:10:1
# @param max_weight float 0.15 Max weight per name range=0.05:0.15:0.01
# @param stop_loss float 0.06 Stop from entry range=0.03:0.12:0.01
# @param trail_stop float 0.08 Trail from peak close range=0.04:0.15:0.01
# @param max_hold_days int 10 Max holding sessions range=3:15:1
# @param halt_drawdown float 0.15 Stop opening after this peak drawdown range=0.08:0.25:0.01


def initialize(context):
    context.set_universe(pool="csi500")
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(30)
    context.set_benchmark("CNStock:000905.SH")
    context.set_metadata(direction_mode="long_only")
    run_daily(trade, time="09:30")


def trade(context, data):
    del data
    if not hasattr(context, "book"):
        context.book = {}
        context.peak_equity = None
    symbols = [symbol for symbol in get_universe_stocks() if _allowed_board(symbol)]
    frames = _load_frames(symbols)
    positions = get_positions()
    _mark_entries(context, positions, frames)
    _exits(context, positions, frames)
    if _halted(context) or _market_blocked(context, frames):
        return
    held = _open_symbols(positions)
    slots = int(context.params.get("holdings", 8)) - len(held)
    if slots <= 0:
        return
    picks = _rank_entries(context, frames, held)
    if not picks:
        return
    weight = min(float(context.params.get("max_weight", 0.15)), 0.95 / max(len(picks), 1))
    for symbol, reason in picks[:slots]:
        context.book[symbol] = {"pending": True}
        order_target_percent(symbol, weight, reason=reason)


def _allowed_board(symbol):
    code = str(symbol).split(":")[-1].upper()
    if code.endswith(".BJ"):
        return False
    number = code.split(".")[0]
    return number.startswith(("60", "00", "30", "68"))


def _load_frames(symbols):
    if not symbols:
        return {}
    fields = [
        "open", "high", "low", "close", "volume",
        "raw_close", "up_limit", "is_st", "suspended",
        "amount", "turnover_rate",
    ]
    loaded = history(40, "1d", fields, symbols)
    if hasattr(loaded, "columns"):
        return {symbols[0]: loaded}
    return loaded or {}


def _mark_entries(context, positions, frames):
    for symbol, position in positions.items():
        if float(position.amount or 0.0) <= 0:
            context.book.pop(symbol, None)
            continue
        state = context.book.get(symbol) or {}
        close = _last(frames.get(symbol), "close")
        if state.get("entry") is None:
            state["entry"] = float(position.avg_cost or 0.0) or close
            state["days"] = 0
            state["peak"] = state["entry"]
        state["pending"] = False
        state["days"] = int(state.get("days") or 0) + 1
        if close is not None:
            state["peak"] = max(float(state.get("peak") or close), close)
        context.book[symbol] = state


def _exits(context, positions, frames):
    stop = float(context.params.get("stop_loss", 0.06))
    trail = float(context.params.get("trail_stop", 0.08))
    limit = int(context.params.get("max_hold_days", 10))
    for symbol, position in list(positions.items()):
        if float(position.amount or 0.0) <= 0:
            continue
        frame = frames.get(symbol)
        close = _last(frame, "close")
        state = context.book.get(symbol) or {}
        entry = state.get("entry")
        reason = None
        if close is None or entry is None:
            continue
        if close <= float(entry) * (1.0 - stop):
            reason = "stop"
        elif close <= float(state.get("peak") or close) * (1.0 - trail):
            reason = "trail"
        elif _below_ma(frame, 5):
            reason = "ma5"
        elif int(state.get("days") or 0) >= limit:
            reason = "time"
        if reason:
            order_target_percent(symbol, 0.0, reason=reason)
            context.book.pop(symbol, None)


def _halted(context):
    equity = float(context.portfolio.total_value or 0.0)
    peak = context.peak_equity
    if peak is None or equity > peak:
        context.peak_equity = equity
        peak = equity
    limit = float(context.params.get("halt_drawdown", 0.15))
    return peak > 0 and equity / peak - 1.0 <= -limit


def _market_blocked(context, frames):
    overnight = context.params.get("us_overnight_return")
    if overnight is not None:
        try:
            if float(overnight) <= -0.015:
                return True
        except (TypeError, ValueError):
            pass
    returns = []
    for frame in frames.values():
        change = _return(frame, 5)
        if change is not None:
            returns.append(change)
    if len(returns) < 5:
        return False
    returns.sort()
    median = returns[len(returns) // 2]
    return median <= -0.03


def _rank_entries(context, frames, held):
    industries = _industries(list(frames), context)
    sector_names = _strong_sectors(frames, industries)
    ranked = []
    for symbol, frame in frames.items():
        if symbol in held or _blocked(frame):
            continue
        reasons = []
        if _volume_breakout(frame):
            reasons.append("breakout")
        if _oversold_reversal(frame):
            reasons.append("reversal")
        if _first_limit_next_day(frame):
            reasons.append("first_board")
        if industries.get(symbol) in sector_names and _return(frame, 5) is not None:
            reasons.append("sector")
        if not reasons:
            continue
        score = len(reasons) * 2.0 + _liquidity(frame)
        ranked.append((score, symbol, "+".join(reasons)))
    ranked.sort(reverse=True)
    return [(symbol, reason) for _score, symbol, reason in ranked]


def _industries(symbols, context):
    del context
    try:
        return get_ashare_industry_map(symbols, None) or {}
    except Exception:
        return {}


def _strong_sectors(frames, industries):
    buckets = {}
    for symbol, industry in industries.items():
        if not industry or symbol not in frames:
            continue
        change = _return(frames[symbol], 5)
        if change is None:
            continue
        buckets.setdefault(industry, []).append(change)
    strength = []
    for industry, changes in buckets.items():
        changes.sort()
        strength.append((changes[len(changes) // 2], industry))
    if len(strength) < 2:
        return set()
    strength.sort(reverse=True)
    keep = max(1, len(strength) // 5)
    return {industry for _value, industry in strength[:keep]}


def _volume_breakout(frame):
    if frame is None or len(frame) < 22:
        return False
    prior = frame.iloc[-21:-1]
    close = _last(frame, "close")
    volume = _last(frame, "volume")
    if close is None or volume is None or prior.empty:
        return False
    return close > float(prior["high"].max()) and volume > float(prior["volume"].mean()) * 1.5


def _oversold_reversal(frame):
    change = _return(frame, 5)
    close = _last(frame, "close")
    opened = _last(frame, "open")
    if change is None or close is None or opened is None:
        return False
    return change <= -0.08 and close > opened


def _first_limit_next_day(frame):
    if frame is None or len(frame) < 3:
        return False
    return _closed_limit_up(frame, -1) and not _closed_limit_up(frame, -2)


def _closed_limit_up(frame, offset):
    if "raw_close" not in frame.columns or "up_limit" not in frame.columns:
        return False
    raw = _num(frame["raw_close"].iloc[offset])
    limit = _num(frame["up_limit"].iloc[offset])
    if raw is None or limit is None or limit <= 0:
        return False
    return raw >= limit - 0.005


def _liquidity(frame):
    score = 0.0
    volume = _ratio(frame, "volume")
    amount = _ratio(frame, "amount")
    turnover = _ratio(frame, "turnover_rate")
    for value in (volume, amount, turnover):
        if value is not None:
            score = max(score, value)
    return score


def _ratio(frame, column):
    if frame is None or column not in frame.columns or len(frame) < 21:
        return None
    base = frame[column].iloc[-21:-1]
    current = _last(frame, column)
    values = [item for item in base.tolist() if _num(item) is not None and _num(item) > 0]
    if current is None or not values:
        return None
    midpoint = sorted(values)[len(values) // 2]
    if midpoint <= 0:
        return None
    return current / midpoint


def _return(frame, days):
    if frame is None or len(frame) <= days:
        return None
    latest = _last(frame, "close")
    earlier = _num(frame["close"].iloc[-1 - days])
    if latest is None or earlier is None or earlier <= 0:
        return None
    return latest / earlier - 1.0


def _below_ma(frame, days):
    if frame is None or len(frame) < days:
        return False
    close = _last(frame, "close")
    window = [_num(item) for item in frame["close"].iloc[-days:].tolist()]
    window = [item for item in window if item is not None]
    if close is None or len(window) < days:
        return False
    return close < sum(window) / float(len(window))


def _blocked(frame):
    if frame is None or len(frame) < 22:
        return True
    if "is_st" in frame.columns and bool(frame["is_st"].iloc[-1]):
        return True
    if "suspended" in frame.columns and bool(frame["suspended"].iloc[-1]):
        return True
    return False


def _open_symbols(positions):
    return {
        symbol
        for symbol, position in positions.items()
        if float(position.amount or 0.0) > 0
    }


def _last(frame, column):
    if frame is None or column not in frame.columns:
        return None
    return _num(frame[column].iloc[-1])


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
