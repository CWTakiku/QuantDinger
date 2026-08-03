import numpy as np
import pandas as pd

from app.services.strategy_v2 import StrategyV2BacktestRunner


def test_v2_strategy_can_compute_builtin_factor_without_future_data():
    close = np.linspace(100.0, 130.0, 40)
    frame = pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(40, 1000),
    }, index=pd.date_range("2026-01-01", periods=40, freq="D"))
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if len(get_history(100, security_list="AAPL")) >= 20:
        context.log("sma=%.2f" % factor("sma", "AAPL", period=20))
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=10000,
    ).run()

    assert result["logs"]
    assert result["logs"][0].startswith("sma=")


def test_get_factors_soft_fails_when_one_symbol_has_insufficient_history():
    good = np.linspace(100.0, 140.0, 80)
    good_frame = pd.DataFrame({
        "open": good,
        "high": good + 1,
        "low": good - 1,
        "close": good,
        "volume": np.full(80, 1000),
    }, index=pd.date_range("2025-01-01", periods=80, freq="D"))
    tiny = good_frame.iloc[:3].copy()
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL", "USStock:NEW"])
    context.subscribe(frequency="1d")
    context.set_warmup(5)
    run_daily(scan, time="15:05")

def scan(context, data):
    scores = get_factors(["USStock:AAPL", "USStock:NEW"], "momentum", period=20)
    g.count = int(scores["momentum"].notna().sum())
    log("ok=%d" % g.count)
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": good_frame, "USStock:NEW": tiny},
        initial_capital=10000,
    ).run()

    # Soft-fail insufficient history to NaN; callback must not abort the run.
    assert any("ok=1" in str(item) for item in (result.get("logs") or []))
