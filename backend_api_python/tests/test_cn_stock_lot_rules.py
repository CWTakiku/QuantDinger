from __future__ import annotations

import pandas as pd

from app.markets.cn_stock.lot_rules import (
    adjust_cn_stock_target,
    cn_stock_lot_spec,
    extract_cn_stock_code,
    is_star_market,
)
from app.services.strategy_v2.runtime import StrategyV2BacktestRunner


def _cn_frame(closes):
    index = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        },
        index=index,
    )


def test_cn_stock_lot_spec_main_and_star():
    assert extract_cn_stock_code("CNStock:600519") == "600519"
    assert extract_cn_stock_code("CNStock:688981.SH") == "688981"
    assert cn_stock_lot_spec("CNStock:600519") == (100.0, 100.0)
    assert cn_stock_lot_spec("CNStock:688981") == (100.0, 200.0)
    assert is_star_market("CNStock:688001")
    assert not is_star_market("CNStock:300750")


def test_adjust_cn_stock_target_rounds_and_enforces_min_open():
    assert adjust_cn_stock_target("CNStock:600519", 0, 130) == 100.0
    assert adjust_cn_stock_target("CNStock:600519", 0, 90) == 0.0
    assert adjust_cn_stock_target("CNStock:688001", 0, 150) == 0.0
    assert adjust_cn_stock_target("CNStock:688001", 0, 250) == 200.0
    assert adjust_cn_stock_target("CNStock:600519", 500, 0) == 0.0
    assert adjust_cn_stock_target("CNStock:600519", 500, 430) == 400.0


def test_backtest_cn_stock_quantities_are_board_lots():
    frame = _cn_frame([10, 10, 10, 10])
    code = """
def initialize(context):
    context.set_universe(["CNStock:600519"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_percent("CNStock:600519", 0.5)
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600519": frame},
        initial_capital=100_000,
        commission=0,
        slippage=0,
    ).run()
    snapshots = result.get("holdingSnapshots") or []
    assert snapshots
    positions = (snapshots[-1].get("positions") or {})
    assert "CNStock:600519" in positions
    qty = float(positions["CNStock:600519"]["quantity"])
    assert qty >= 100
    assert abs(qty % 100) < 1e-9


def test_backtest_star_market_min_open_is_two_lots():
    # Capital only affords ~150 shares at 100 yuan → below STAR min 200 → no fill.
    frame = _cn_frame([100, 100, 100])
    code = """
def initialize(context):
    context.set_universe(["CNStock:688001"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    order_target_value("CNStock:688001", 15000)
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:688001": frame},
        initial_capital=20_000,
        commission=0,
        slippage=0,
    ).run()
    statuses = {item["statusReason"] for item in result.get("orderLedger") or []}
    assert "minimum_trade_unit" in statuses
    last = (result.get("holdingSnapshots") or [{}])[-1]
    assert not (last.get("positions") or {})
