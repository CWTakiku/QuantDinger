"""A-share session dates, T+1, and the cash-equity fee stack."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.markets.cn_stock.costs import ashare_trading_fee
from app.services.market_schedule import equity_bar_session_date
from app.services.strategy_v2.data import MultiAssetDataPortal
from app.services.strategy_v2.runtime import MultiAssetSimulationBroker, OrderIntent, StrategyV2BacktestRunner


def _frame(prices, **extra):
    index = pd.date_range("2026-01-01", periods=len(prices), freq="D")
    data = {
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [100000] * len(prices),
    }
    data.update(extra)
    return pd.DataFrame(data, index=index)


def _portal(symbol, frame):
    return MultiAssetDataPortal({symbol: frame})


def _buy(symbol, amount):
    return OrderIntent(symbol, "quantity", amount)


def test_shanghai_midnight_bar_keeps_the_session_date():
    unix = int(datetime(2026, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    naive = pd.to_datetime(unix, unit="s", utc=True).tz_convert(None)

    assert naive.date().isoformat() == "2026-09-29"
    assert equity_bar_session_date(naive, "CNStock") == date(2026, 9, 30)
    assert equity_bar_session_date(pd.Timestamp("2026-09-30"), "USStock") == date(2026, 9, 30)


def test_ashare_fee_adds_minimum_commission_transfer_and_sell_stamp():
    assert ashare_trading_fee(10_000, 0.0003, selling=False) == 5.1
    assert ashare_trading_fee(10_000, 0.0003, selling=True) == 10.1
    assert ashare_trading_fee(0, 0.0003, selling=True) == 0


def test_cn_stock_cannot_sell_shares_bought_the_same_session():
    symbol = "CNStock:600519.SH"
    frame = _frame([10.0, 10.0])
    portal = _portal(symbol, frame)
    broker = MultiAssetSimulationBroker(initial_capital=100_000, commission=0, slippage=0)

    broker.execute([_buy(symbol, 200), _buy(symbol, -200)], portal, frame.index[0])

    assert broker.portfolio.positions[symbol].amount == 200
    assert any(item.get("statusReason") == "t_plus_one" for item in broker.order_ledger)
    broker.execute([_buy(symbol, -200)], portal, frame.index[1])
    assert symbol not in broker.portfolio.positions
    assert [item["side"] for item in broker.executions] == ["buy", "sell"]


def test_cn_stock_buy_reserves_the_minimum_commission():
    symbol = "CNStock:600519.SH"
    frame = _frame([10.0])
    portal = _portal(symbol, frame)
    broker = MultiAssetSimulationBroker(initial_capital=10_000, commission=0, slippage=0)

    broker.execute([_buy(symbol, 1000)], portal, frame.index[0])

    filled = broker.executions[0]
    assert filled["quantity"] < 1000
    assert filled["quantity"] * 10 + filled["commission"] <= 10_000 + 1e-6
    assert broker.portfolio.available_cash >= -1e-6


def test_us_stock_can_round_trip_on_the_same_bar():
    symbol = "USStock:AAPL"
    frame = _frame([10.0])
    portal = _portal(symbol, frame)
    broker = MultiAssetSimulationBroker(initial_capital=100_000, commission=0, slippage=0)

    broker.execute([_buy(symbol, 1), _buy(symbol, -1)], portal, frame.index[0])

    assert symbol not in broker.portfolio.positions
    assert [item["side"] for item in broker.executions] == ["buy", "sell"]


def test_cn_stock_backtest_charges_stamp_on_the_sell():
    symbol = "CNStock:600519.SH"
    frame = _frame([10.0, 10.0])
    portal = _portal(symbol, frame)
    broker = MultiAssetSimulationBroker(initial_capital=100_000, commission=0.0003, slippage=0)

    broker.execute([_buy(symbol, 1000)], portal, frame.index[0])
    broker.execute([_buy(symbol, -1000)], portal, frame.index[1])

    fees = [round(float(item["commission"]), 6) for item in broker.executions]
    assert fees == [5.1, 10.1]

    class _Runner:
        pass

    holder = _Runner()
    holder.broker = broker
    assert StrategyV2BacktestRunner._reconcile(holder)["feeMismatchIndexes"] == []


def test_limit_up_bar_blocks_the_buy():
    symbol = "CNStock:600519.SH"
    frame = _frame([10.0, 10.0], limit_up=[True, False], is_limit_up=[True, False])
    portal = _portal(symbol, frame)
    broker = MultiAssetSimulationBroker(initial_capital=100_000, commission=0, slippage=0)

    broker.execute([_buy(symbol, 100)], portal, frame.index[0])
    assert broker.executions == []
    assert any(item.get("statusReason") == "limit_up" for item in broker.order_ledger)

    broker.execute([_buy(symbol, 100)], portal, frame.index[1])
    assert len(broker.executions) == 1
    assert not str(broker.executions[0]["time"]).startswith("2026-01-01")


def test_live_intent_with_canonical_key_matches_spot_member():
    from app.services.trading_executor import TradingExecutor

    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_: []
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return True

    executor._execute_signal = execute
    member = {"key": "CNStock:000002.SZ@spot", "symbol": "000002.SZ", "market": "CNStock", "market_type": "spot"}
    frame = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-10-08"]))
    assert executor._execute_strategy_v2_intent(
        strategy_id=1, strategy_name="alpha",
        intent=OrderIntent(symbol="CNStock:000002.SZ", kind="target_percent", value=0.05),
        frames={member["key"]: frame}, candidates=[member], initial_capital=100000, leverage=1,
        execution_mode="signal", notification_config={}, trading_config={}, exchange_config={},
        signal_ts=1, strategy_run_id=1,
    )
    assert captured["symbol"] == "000002.SZ"
    assert captured["market_frame"] is frame


def test_live_intent_at_limit_up_is_skipped(monkeypatch):
    from app.services import trading_executor
    from app.services.trading_executor import TradingExecutor

    logs = []
    monkeypatch.setattr(trading_executor, "append_strategy_log", lambda *args: logs.append(args))
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_: []
    executor._execute_signal = lambda **_: pytest.fail("order must not be sent at limit up")
    member = {"key": "CNStock:600048.SH@spot", "symbol": "600048.SH", "market": "CNStock", "market_type": "spot"}
    frame = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-10-08"]))

    submitted = executor._execute_strategy_v2_intent(
        strategy_id=1, strategy_name="alpha",
        intent=OrderIntent(symbol="CNStock:600048.SH", kind="target_percent", value=0.05),
        frames={member["key"]: frame}, candidates=[member], initial_capital=100000, leverage=1,
        execution_mode="signal", notification_config={}, trading_config={}, exchange_config={},
        signal_ts=1, strategy_run_id=1, current_price_override=11.0,
        ashare_quote={"last": 11.0, "previousClose": 10.0, "high": 11.0, "low": 10.1},
    )

    assert submitted is False
    assert any("limit_up" in str(entry) for entry in logs)


def test_live_intent_without_quote_is_skipped(monkeypatch):
    from app.services import trading_executor
    from app.services.trading_executor import TradingExecutor

    logs = []
    monkeypatch.setattr(trading_executor, "append_strategy_log", lambda *args: logs.append(args))
    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_: []
    executor._execute_signal = lambda **_: pytest.fail("order must not be sent without a live quote")
    member = {"key": "CNStock:600048.SH@spot", "symbol": "600048.SH", "market": "CNStock", "market_type": "spot"}
    frame = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-10-08"]))

    submitted = executor._execute_strategy_v2_intent(
        strategy_id=1, strategy_name="alpha",
        intent=OrderIntent(symbol="CNStock:600048.SH", kind="target_percent", value=0.05),
        frames={member["key"]: frame}, candidates=[member], initial_capital=100000, leverage=1,
        execution_mode="signal", notification_config={}, trading_config={}, exchange_config={},
        signal_ts=1, strategy_run_id=1, ashare_quote={},
    )

    assert submitted is False
    assert any("no_quote" in str(entry) for entry in logs)
