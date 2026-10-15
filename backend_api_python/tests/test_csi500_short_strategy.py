from datetime import date
from pathlib import Path

import pandas as pd

from app.services.ashare_data.bars import build_backtest_rows
from app.services.strategy_v2 import StrategyV2BacktestRunner, compile_strategy_v2

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "strategy_v2_csi500_short.py"
)


def _code() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


def test_short_strategy_compiles_as_a_daily_csi500_book():
    manifest = compile_strategy_v2(_code()).manifest
    assert manifest.strategy_type == "portfolio"
    assert manifest.universe.reference == "POOL:csi500"
    assert manifest.schedules[0].time == "09:30"


def test_backtest_rows_keep_amount_and_turnover():
    (row,) = build_backtest_rows(
        [
            {
                "trade_date": date(2024, 1, 2),
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
                "amount": 10200.0,
                "turnover_rate": 1.5,
                "adj_factor": 1.0,
                "up_limit": 11.0,
                "down_limit": 9.0,
                "is_st": False,
            }
        ]
    )
    assert row["amount"] == 10200.0
    assert row["turnover_rate"] == 1.5


def test_breakout_buys_the_main_board_name_and_skips_beijing(monkeypatch):
    monkeypatch.setattr(
        "app.services.strategy_v2.runtime.get_ashare_industry_map",
        lambda symbols, as_of: {},
    )
    symbols = [f"CNStock:60000{i}.SH" for i in range(8)]
    symbols.append("CNStock:830001.BJ")
    frames = {symbol: _quiet() for symbol in symbols}
    frames["CNStock:600000.SH"] = _breakout()
    frames["CNStock:830001.BJ"] = _breakout(close=20.0, volume=9000.0)
    result = StrategyV2BacktestRunner(
        code=_code(),
        frames=frames,
        initial_capital=500_000.0,
        commission=0.00025,
        slippage=0.002,
        params={"holdings": 8, "stop_loss": 0.06, "trail_stop": 0.5},
        universe_resolver=lambda _ref, _when: list(symbols),
    ).run()
    bought = {item.get("symbol") for item in result["executions"] if item.get("side") == "buy"}
    sold = {item.get("symbol") for item in result["executions"] if item.get("side") == "sell"}
    assert "CNStock:600000.SH" in bought
    assert "CNStock:830001.BJ" not in bought
    assert "CNStock:600000.SH" in sold


def _quiet(price: float = 10.0, volume: float = 1000.0, periods: int = 40) -> pd.DataFrame:
    index = pd.date_range("2024-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "open": [price] * periods,
            "high": [price * 1.01] * periods,
            "low": [price * 0.99] * periods,
            "close": [price] * periods,
            "volume": [volume] * periods,
            "raw_close": [price] * periods,
            "up_limit": [price * 1.1] * periods,
            "is_st": [False] * periods,
            "suspended": [False] * periods,
        },
        index=index,
    )


def _breakout(close: float = 13.0, volume: float = 4000.0) -> pd.DataFrame:
    frame = _quiet()
    signal = 28
    fill = 29
    frame.iloc[signal, frame.columns.get_loc("open")] = 10.2
    frame.iloc[signal, frame.columns.get_loc("high")] = close
    frame.iloc[signal, frame.columns.get_loc("close")] = close
    frame.iloc[signal, frame.columns.get_loc("volume")] = volume
    frame.iloc[fill, frame.columns.get_loc("open")] = close
    frame.iloc[fill, frame.columns.get_loc("close")] = close
    for offset in range(fill + 1, len(frame)):
        frame.iloc[offset, frame.columns.get_loc("open")] = 8.0
        frame.iloc[offset, frame.columns.get_loc("high")] = 8.2
        frame.iloc[offset, frame.columns.get_loc("low")] = 7.8
        frame.iloc[offset, frame.columns.get_loc("close")] = 8.0
    return frame
