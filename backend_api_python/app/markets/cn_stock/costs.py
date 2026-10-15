"""A-share cash-equity trading costs.

Brokerage uses the caller-supplied commission rate, with a 5 CNY minimum
per fill. Stamp duty is 0.1% on sells before 2023-08-28 and 0.05% from that
day. The CSDC transfer fee is 0.002% on both sides before 2022-04-29 and
0.001% from that day. A missing trade date uses the current rates.
"""

from __future__ import annotations

from datetime import date, datetime

STAMP_DUTY_RATE = 0.0005
TRANSFER_FEE_RATE = 0.00001
STAMP_DUTY_CUTOVER = date(2023, 8, 28)
TRANSFER_FEE_CUTOVER = date(2022, 4, 29)
MIN_COMMISSION = 5.0


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def stamp_duty_rate(trade_date: object = None) -> float:
    day = _as_date(trade_date)
    if day is not None and day < STAMP_DUTY_CUTOVER:
        return 0.001
    return STAMP_DUTY_RATE


def transfer_fee_rate(trade_date: object = None) -> float:
    day = _as_date(trade_date)
    if day is not None and day < TRANSFER_FEE_CUTOVER:
        return 0.00002
    return TRANSFER_FEE_RATE


def ashare_trading_fee(
    notional: float,
    commission_rate: float,
    *,
    selling: bool,
    trade_date: object = None,
    min_commission: float | None = None,
) -> float:
    amount = abs(float(notional or 0.0))
    if amount <= 0:
        return 0.0
    floor = MIN_COMMISSION if min_commission is None else max(0.0, float(min_commission))
    brokerage = max(floor, amount * max(0.0, float(commission_rate or 0.0)))
    transfer = amount * transfer_fee_rate(trade_date)
    stamp = amount * stamp_duty_rate(trade_date) if selling else 0.0
    return brokerage + transfer + stamp


def max_buy_notional(
    spendable: float,
    commission_rate: float,
    *,
    trade_date: object = None,
    min_commission: float | None = None,
) -> float:
    """Largest buy notional that still leaves the A-share fee inside ``spendable``."""
    cash = float(spendable or 0.0)
    floor = MIN_COMMISSION if min_commission is None else max(0.0, float(min_commission))
    if cash <= floor:
        return 0.0
    rate = max(0.0, float(commission_rate or 0.0))
    transfer = transfer_fee_rate(trade_date)
    floor_notional = (cash - floor) / (1.0 + transfer)
    if floor_notional * rate <= floor:
        return max(0.0, floor_notional)
    return max(0.0, cash / (1.0 + transfer + rate))


def execution_fee(
    symbol: object,
    notional: float,
    commission_rate: float,
    *,
    selling: bool,
    trade_date: object = None,
    min_commission: float | None = None,
) -> float:
    from app.markets.cn_stock.lot_rules import cn_stock_lot_spec

    amount = abs(float(notional or 0.0))
    if cn_stock_lot_spec(str(symbol or "")) is not None:
        return ashare_trading_fee(
            amount,
            commission_rate,
            selling=selling,
            trade_date=trade_date,
            min_commission=min_commission,
        )
    return amount * max(0.0, float(commission_rate or 0.0))
