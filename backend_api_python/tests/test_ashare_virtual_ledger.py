"""A-share rules in the signal-mode virtual ledger and pre-trade quote checks."""

from contextlib import contextmanager

import pytest

from app.markets.cn_stock.quote_rules import ashare_quote_block_reason
from app.services import virtual_trading


class _Cursor:
    def __init__(self, *, position, bought_today):
        self.position = position
        self.bought_today = bought_today
        self.sql = ""
        self.calls = []

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.calls.append((self.sql, params))

    def fetchone(self):
        sql = self.sql
        if "FROM qd_strategies_trading" in sql:
            return {"id": 1, "user_id": 1, "execution_mode": "signal", "initial_capital": 100000,
                    "exchange_config": {}, "trading_config": {}, "market_type": "spot",
                    "leverage": 1, "market_category": "CNStock"}
        if "FROM qd_strategy_virtual_orders" in sql:
            return {}
        if "SELECT * FROM qd_strategy_virtual_accounts" in sql:
            return {"initial_cash": 100000, "realized_pnl": 0, "total_commission": 0}
        if "SELECT * FROM qd_strategy_virtual_positions" in sql:
            return self.position
        if "SUM(amount)" in sql:
            return {"bought": self.bought_today}
        if "RETURNING id" in sql:
            return {"id": 7}
        if "SUM(unrealized_pnl)" in sql:
            return {"unrealized": 0}
        return {}

    def close(self):
        pass

    def inserted(self, table):
        return [params for sql, params in self.calls if sql.startswith(f"INSERT INTO {table}")]


def _run(monkeypatch, *, action, amount, price, position=None, bought_today=0.0):
    cursor = _Cursor(position=position or {}, bought_today=bought_today)

    class _Db:
        def cursor(self):
            return cursor

        def commit(self):
            pass

    @contextmanager
    def _conn():
        yield _Db()

    monkeypatch.setattr(virtual_trading, "get_db_connection", _conn)
    result = virtual_trading.execute_virtual_signal_order(
        {"id": 11, "user_id": 1, "strategy_id": 1, "execution_mode": "signal"},
        {"strategy_id": 1, "execution_mode": "signal", "signal_type": action,
         "symbol": "CNStock:600048.SH", "amount": amount, "ref_price": price,
         "_virtual_fill_price": price},
    )
    return result, cursor


def test_buy_charges_ashare_fee_with_minimum(monkeypatch):
    result, cursor = _run(monkeypatch, action="open_long", amount=100, price=10.0)

    assert result["status"] == "filled"
    # 1,000 notional: 5 CNY minimum brokerage + 0.001% transfer fee, no stamp duty.
    assert result["commission"] == pytest.approx(5.0 + 1000 * 0.00001)
    fill = cursor.inserted("strategy_order_fills")[0]
    assert "CNY" in fill


def test_sell_adds_stamp_duty(monkeypatch):
    result, _ = _run(
        monkeypatch, action="close_long", amount=0, price=10.0,
        position={"size": 10000, "entry_price": 9.0},
    )

    notional = 100000.0
    expected = notional * 0.00025 + notional * 0.00001 + notional * 0.0005
    assert result["status"] == "filled"
    assert result["commission"] == pytest.approx(expected)


def test_t_plus_one_blocks_selling_shares_bought_today(monkeypatch):
    result, cursor = _run(
        monkeypatch, action="close_long", amount=0, price=10.0,
        position={"size": 1000, "entry_price": 9.0}, bought_today=1000,
    )

    assert result["status"] == "rejected"
    assert result["fill_quantity"] == 0
    assert cursor.inserted("qd_strategy_virtual_trades") == []
    order = cursor.inserted("qd_strategy_virtual_orders")[0]
    assert "t_plus_one" in order


def test_t_plus_one_sells_only_settled_shares(monkeypatch):
    result, _ = _run(
        monkeypatch, action="close_long", amount=0, price=10.0,
        position={"size": 1500, "entry_price": 9.0}, bought_today=500,
    )

    assert result["status"] == "filled"
    assert result["fill_quantity"] == 1000


def test_ashare_short_is_rejected(monkeypatch):
    result, cursor = _run(monkeypatch, action="open_short", amount=100, price=10.0)

    assert result["status"] == "rejected"
    assert cursor.inserted("qd_strategy_virtual_trades") == []
    assert "ashare_short_unsupported" in cursor.inserted("qd_strategy_virtual_orders")[0]


def test_buy_is_floored_to_board_lot(monkeypatch):
    result, _ = _run(monkeypatch, action="open_long", amount=1250, price=10.0)

    assert result["status"] == "filled"
    assert result["fill_quantity"] == 1200


def test_buy_below_one_lot_is_rejected(monkeypatch):
    result, cursor = _run(monkeypatch, action="open_long", amount=60, price=10.0)

    assert result["status"] == "rejected"
    assert "below_board_lot" in cursor.inserted("qd_strategy_virtual_orders")[0]


def test_partial_sell_is_floored_but_full_exit_keeps_odd_lot(monkeypatch):
    partial, _ = _run(
        monkeypatch, action="reduce_long", amount=250, price=10.0,
        position={"size": 1050, "entry_price": 9.0},
    )
    full, _ = _run(
        monkeypatch, action="close_long", amount=0, price=10.0,
        position={"size": 1050, "entry_price": 9.0},
    )

    assert partial["fill_quantity"] == 200
    assert full["fill_quantity"] == 1050


@pytest.mark.parametrize("quote", [None, {}, {"last": 0, "previousClose": 10.0, "high": 10.2, "low": 9.9}])
def test_missing_quote_blocks_order(quote):
    assert ashare_quote_block_reason(quote, "CNStock:600048.SH", buying=True) == "no_quote"


@pytest.mark.parametrize("quote, symbol, buying, expected", [
    ({"last": 11.0, "previousClose": 10.0, "high": 11.0, "low": 10.2}, "CNStock:600048.SH", True, "limit_up"),
    ({"last": 11.0, "previousClose": 10.0, "high": 11.0, "low": 10.2}, "CNStock:600048.SH", False, ""),
    ({"last": 9.0, "previousClose": 10.0, "high": 9.5, "low": 9.0}, "CNStock:600048.SH", False, "limit_down"),
    ({"last": 11.0, "previousClose": 10.0, "high": 11.0, "low": 10.2}, "CNStock:300274.SZ", True, ""),
    ({"last": 12.0, "previousClose": 10.0, "high": 12.0, "low": 11.0}, "CNStock:688126.SH", True, "limit_up"),
    ({"last": 15.56, "previousClose": 15.56, "high": 0.0, "low": 0.0}, "CNStock:601059.SH", True, "suspended"),
])
def test_quote_block_reason(quote, symbol, buying, expected):
    assert ashare_quote_block_reason(quote, symbol, buying=buying) == expected


def test_st_stock_uses_five_percent_band():
    quote = {"last": 10.5, "previousClose": 10.0, "high": 10.5, "low": 10.0}
    assert ashare_quote_block_reason(quote, "CNStock:600001.SH", buying=True, st=True) == "limit_up"
    assert ashare_quote_block_reason(quote, "CNStock:600001.SH", buying=True) == ""
