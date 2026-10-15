"""Tradable long-only book for an account that cannot hold the whole index.

Names are chosen with a rank buffer. Orders round to board lots, pay the
A-share fee schedule, and refuse a limit-up buy or a limit-down sell.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.markets.cn_stock.costs import ashare_trading_fee, max_buy_notional
from app.markets.cn_stock.lot_rules import adjust_cn_stock_target

PRICE_TICK = 0.005


def cash_dividend(shares: float, raw_close: float, factor_prev: float, factor: float) -> float:
    """Cash that keeps mark-to-market equal to the adjusted price path.

    A split or dividend moves the raw close and the adjustment factor in
    opposite directions. Crediting this amount leaves the position value on
    the adjusted path without rewriting the share count.
    """
    if shares == 0 or raw_close <= 0 or factor_prev <= 0 or factor <= 0:
        return 0.0
    if not np.isfinite([shares, raw_close, factor_prev, factor]).all():
        return 0.0
    return float(shares) * float(raw_close) * (float(factor) / float(factor_prev) - 1.0)


def select_names(
    score: pd.Series,
    held: list[str],
    can_hold: set[str],
    can_buy: set[str],
    can_sell: set[str],
    *,
    holdings: int = 40,
    buffer: int = 60,
) -> list[str]:
    """Keep names still inside the buffer, then fill vacancies from the top ranks."""
    ranked = pd.to_numeric(score, errors="coerce").dropna().rank(ascending=False, method="first")
    forced: list[str] = []
    desired: list[str] = []
    for symbol in held:
        if symbol not in can_sell:
            forced.append(symbol)
            continue
        rank = float(ranked[symbol]) if symbol in ranked.index else float("inf")
        if symbol in can_hold and rank <= buffer:
            desired.append(symbol)
    desired.sort(key=lambda symbol: float(ranked[symbol]))
    room = max(int(holdings) - len(forced), 0)
    kept = forced + desired[:room]
    kept_set = set(kept)
    slots = max(int(holdings) - len(kept_set), 0)
    buys: list[str] = []
    if slots > 0 and not ranked.empty:
        for symbol in ranked.sort_values().index:
            if symbol in kept_set or symbol not in can_buy:
                continue
            if float(ranked[symbol]) > holdings:
                break
            buys.append(str(symbol))
            if len(buys) >= slots:
                break
    return [str(symbol) for symbol in kept + buys]


def simulate(
    *,
    raw_open: pd.DataFrame,
    raw_close: pd.DataFrame,
    adj_factor: pd.DataFrame,
    score: pd.DataFrame,
    can_hold: pd.DataFrame,
    can_buy: pd.DataFrame,
    can_sell: pd.DataFrame,
    trade_positions: list[int],
    capital: float = 500_000.0,
    commission: float = 0.00025,
    slippage: float = 0.001,
    holdings: int = 40,
    buffer: int = 60,
    max_weight: float = 0.04,
) -> dict[str, object]:
    """Trade next-session opens from the previous close's score."""
    dates = list(raw_close.index)
    cash = float(capital)
    shares: dict[str, float] = {}
    equity_points: list[tuple[pd.Timestamp, float]] = []
    turnovers: list[tuple[pd.Timestamp, float]] = []
    holding_counts: list[tuple[pd.Timestamp, int]] = []
    fees = 0.0
    fills = 0
    trade_at = set(int(pos) for pos in trade_positions)

    for pos, stamp in enumerate(dates):
        prev_shares = dict(shares)
        if pos > 0:
            cash += _dividend_cash(prev_shares, raw_close, adj_factor, pos)
        if pos in trade_at and pos > 0:
            equity = cash + _mark(shares, raw_open.iloc[pos])
            cash, shares, fee_paid, traded, turnover = _rebalance(
                shares=shares,
                cash=cash,
                equity=equity,
                stamp=pd.Timestamp(stamp),
                score=score.iloc[pos - 1],
                can_hold=set(can_hold.columns[can_hold.iloc[pos - 1].fillna(False).to_numpy(dtype=bool)]),
                can_buy=set(can_buy.columns[can_buy.iloc[pos].fillna(False).to_numpy(dtype=bool)]),
                can_sell=set(can_sell.columns[can_sell.iloc[pos].fillna(False).to_numpy(dtype=bool)]),
                raw_open=raw_open.iloc[pos],
                up_limit=None,
                commission=commission,
                slippage=slippage,
                holdings=holdings,
                buffer=buffer,
                max_weight=max_weight,
            )
            fees += fee_paid
            fills += traded
            turnovers.append((pd.Timestamp(stamp), turnover))
        marked = cash + _mark(shares, raw_close.iloc[pos])
        equity_points.append((pd.Timestamp(stamp), marked))
        holding_counts.append((pd.Timestamp(stamp), sum(1 for qty in shares.values() if qty > 0)))

    equity = pd.Series({stamp: value for stamp, value in equity_points}, dtype=float).sort_index()
    return {
        "equity": equity,
        "fees": fees,
        "fills": fills,
        "turnover": pd.Series({stamp: value for stamp, value in turnovers}, dtype=float),
        "holdings": pd.Series({stamp: value for stamp, value in holding_counts}, dtype=float),
        "final_shares": shares,
    }


def blocked_limit(raw_open: pd.DataFrame, limit_price: pd.DataFrame, *, buying: bool) -> pd.DataFrame:
    """True when the open is already through the limit, so the side cannot trade."""
    open_px = _numeric_frame(raw_open)
    limit_px = _numeric_frame(limit_price)
    if buying:
        return open_px >= limit_px - PRICE_TICK
    return open_px <= limit_px + PRICE_TICK


def _numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    values = pd.to_numeric(np.asarray(frame.to_numpy()).ravel(), errors="coerce")
    return pd.DataFrame(np.asarray(values, dtype=float).reshape(frame.shape), index=frame.index, columns=frame.columns)


def _rebalance(
    *,
    shares: dict[str, float],
    cash: float,
    equity: float,
    stamp: pd.Timestamp,
    score: pd.Series,
    can_hold: set[str],
    can_buy: set[str],
    can_sell: set[str],
    raw_open: pd.Series,
    up_limit: object,
    commission: float,
    slippage: float,
    holdings: int,
    buffer: int,
    max_weight: float,
) -> tuple[float, dict[str, float], float, int, float]:
    del up_limit
    held = [symbol for symbol, qty in shares.items() if qty > 0]
    selected = select_names(
        score,
        held,
        can_hold,
        can_buy,
        can_sell,
        holdings=holdings,
        buffer=buffer,
    )
    fee_paid = 0.0
    traded_notional = 0.0
    fills = 0
    for symbol in list(shares):
        if symbol in selected or shares.get(symbol, 0.0) <= 0:
            continue
        if symbol not in can_sell:
            continue
        price = _sell_price(raw_open.get(symbol), slippage)
        if price is None:
            continue
        qty = float(shares.get(symbol, 0.0))
        notional = qty * price
        fee = ashare_trading_fee(notional, commission, selling=True, trade_date=_trade_day(stamp))
        cash += notional - fee
        fee_paid += fee
        traded_notional += notional
        fills += 1
        shares.pop(symbol, None)

    equity = cash + _mark(shares, raw_open)
    targets = _target_shares(
        selected,
        shares,
        raw_open,
        equity=equity,
        max_weight=max_weight,
        slippage=slippage,
    )
    rank = pd.to_numeric(score, errors="coerce").rank(ascending=False, method="first")
    buy_order = sorted(selected, key=lambda symbol: float(rank.get(symbol, 1e9)))
    for symbol in buy_order:
        current = float(shares.get(symbol, 0.0))
        target = float(targets.get(symbol, current))
        if target <= current + 1e-9 or symbol not in can_buy:
            continue
        price = _buy_price(raw_open.get(symbol), slippage)
        if price is None:
            continue
        affordable = max_buy_notional(cash, commission, trade_date=_trade_day(stamp))
        capped_qty = current + affordable / price
        qty = adjust_cn_stock_target(symbol, current, min(target, capped_qty))
        delta = qty - current
        if delta <= 1e-9:
            continue
        notional = delta * price
        fee = ashare_trading_fee(notional, commission, selling=False, trade_date=_trade_day(stamp))
        if cash + 1e-6 < notional + fee:
            continue
        cash -= notional + fee
        fee_paid += fee
        traded_notional += notional
        fills += 1
        shares[symbol] = qty
    turnover = traded_notional / (2.0 * equity) if equity > 0 else 0.0
    return cash, shares, fee_paid, fills, turnover


def _target_shares(
    selected: list[str],
    shares: dict[str, float],
    raw_open: pd.Series,
    *,
    equity: float,
    max_weight: float,
    slippage: float,
) -> dict[str, float]:
    if not selected or equity <= 0:
        return {}
    base = equity / len(selected)
    cap = max_weight * equity
    targets: dict[str, float] = {}
    for symbol in selected:
        price = _buy_price(raw_open.get(symbol), slippage)
        current = float(shares.get(symbol, 0.0))
        if price is None:
            targets[symbol] = current
            continue
        desired = min(base, cap) / price
        targets[symbol] = adjust_cn_stock_target(symbol, current, desired)
    return targets


def _dividend_cash(
    shares: dict[str, float],
    raw_close: pd.DataFrame,
    adj_factor: pd.DataFrame,
    pos: int,
) -> float:
    total = 0.0
    current = raw_close.iloc[pos]
    factor_prev = adj_factor.iloc[pos - 1]
    factor = adj_factor.iloc[pos]
    for symbol, qty in shares.items():
        total += cash_dividend(
            qty,
            _num(current.get(symbol)),
            _num(factor_prev.get(symbol)),
            _num(factor.get(symbol)),
        )
    return total


def _mark(shares: dict[str, float], prices: pd.Series) -> float:
    total = 0.0
    for symbol, qty in shares.items():
        price = _num(prices.get(symbol))
        if qty > 0 and price > 0:
            total += qty * price
    return total


def _buy_price(raw_open: object, slippage: float) -> float | None:
    price = _num(raw_open)
    if price <= 0:
        return None
    return price * (1.0 + slippage)


def _sell_price(raw_open: object, slippage: float) -> float | None:
    price = _num(raw_open)
    if price <= 0:
        return None
    return price * (1.0 - slippage)


def _trade_day(stamp: pd.Timestamp) -> date:
    return pd.Timestamp(stamp).date()


def _num(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(number):
        return 0.0
    return number

