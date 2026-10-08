"""A-share cash-equity trading costs.

Brokerage uses the caller-supplied commission rate, with a 5 CNY minimum
per fill. Stamp duty is 0.05% on sells (the rate in force since 2023-08-28).
The CSDC transfer fee is 0.001% on both sides.
"""

from __future__ import annotations

STAMP_DUTY_RATE = 0.0005
TRANSFER_FEE_RATE = 0.00001
MIN_COMMISSION = 5.0


def ashare_trading_fee(notional: float, commission_rate: float, *, selling: bool) -> float:
    amount = abs(float(notional or 0.0))
    if amount <= 0:
        return 0.0
    brokerage = max(MIN_COMMISSION, amount * max(0.0, float(commission_rate or 0.0)))
    transfer = amount * TRANSFER_FEE_RATE
    stamp = amount * STAMP_DUTY_RATE if selling else 0.0
    return brokerage + transfer + stamp


def max_buy_notional(spendable: float, commission_rate: float) -> float:
    """Largest buy notional that still leaves the A-share fee inside ``spendable``."""
    cash = float(spendable or 0.0)
    if cash <= MIN_COMMISSION:
        return 0.0
    rate = max(0.0, float(commission_rate or 0.0))
    floor_notional = (cash - MIN_COMMISSION) / (1.0 + TRANSFER_FEE_RATE)
    if floor_notional * rate <= MIN_COMMISSION:
        return max(0.0, floor_notional)
    return max(0.0, cash / (1.0 + TRANSFER_FEE_RATE + rate))


def execution_fee(symbol: object, notional: float, commission_rate: float, *, selling: bool) -> float:
    from app.markets.cn_stock.lot_rules import cn_stock_lot_spec

    amount = abs(float(notional or 0.0))
    if cn_stock_lot_spec(str(symbol or "")) is not None:
        return ashare_trading_fee(amount, commission_rate, selling=selling)
    return amount * max(0.0, float(commission_rate or 0.0))
