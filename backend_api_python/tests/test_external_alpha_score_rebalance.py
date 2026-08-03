"""Smoke: external alpha scores drive weekly rebalance or skip when empty."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.services.strategy_v2 import StrategyV2BacktestRunner, compile_strategy_v2

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "examples"
    / "strategy_v2_external_alpha_score_weekly.py"
)

SYMBOLS = [
    "CNStock:600519.SH",
    "CNStock:000001.SZ",
    "CNStock:600036.SH",
]


def _strategy_code() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def _frame(rank: int, periods: int = 28) -> pd.DataFrame:
    index = pd.date_range("2026-07-20", periods=periods, freq="B")
    prices = [100.0 + rank * 5.0 + offset * 0.1 for offset in range(periods)]
    return pd.DataFrame(
        {
            "open": [price * 0.998 for price in prices],
            "high": [price * 1.012 for price in prices],
            "low": [price * 0.988 for price in prices],
            "close": prices,
            "volume": [1_000_000.0 + rank * 10_000.0] * periods,
        },
        index=index,
    )


def _run_backtest(monkeypatch, scores_factory, *, params: dict | None = None) -> dict:
    monkeypatch.setattr(
        "app.services.strategy_v2.runtime.get_external_alpha_scores",
        scores_factory,
    )
    frames = {symbol: _frame(index) for index, symbol in enumerate(SYMBOLS)}
    frames["CNStock:000300.SH"] = _frame(10)
    resolver = lambda _ref, _when, _symbols=SYMBOLS: list(_symbols)
    runtime_params = {
        "source": "external",
        "version": "default",
        "top_n": 3,
        "min_names": 3,
        "score_lag_days": 0,
    }
    if params:
        runtime_params.update(params)
    return StrategyV2BacktestRunner(
        code=_strategy_code(),
        frames=frames,
        initial_capital=100_000.0,
        commission=0.0,
        slippage=0.0,
        params=runtime_params,
        universe_resolver=resolver,
    ).run()


def test_external_alpha_example_compiles():
    manifest = compile_strategy_v2(_strategy_code()).manifest
    assert manifest.strategy_type == "portfolio"
    assert manifest.universe.kind == "dynamic"


def test_external_alpha_rebalance_with_scores_issues_equal_weight_targets(monkeypatch):
    scores = pd.Series(
        {
            "CNStock:600519.SH": 1.25,
            "CNStock:000001.SZ": 0.85,
            "CNStock:600036.SH": 0.42,
        }
    )

    def _scores(_as_of, _source, version=None, symbols=None):
        del version, symbols
        return scores.copy()

    result = _run_backtest(monkeypatch, _scores)

    assert result["totalExecutions"] > 0
    assert any("ext_alpha rebalance" in log for log in result["logs"])

    target_weights: dict[str, float] = {}
    for record in result.get("rebalanceRecords") or []:
        weights = record.get("targetWeights") or {}
        if weights:
            target_weights = {str(k): float(v) for k, v in weights.items()}
            break

    expected = 1.0 / len(SYMBOLS)
    assert target_weights, "expected rebalance target weights"
    for symbol in SYMBOLS:
        assert abs(target_weights.get(symbol, 0.0) - expected) < 1e-6


def test_external_alpha_rebalance_requires_version(monkeypatch):
    def _scores(_as_of, _source, version=None, symbols=None):
        del version, symbols
        return pd.Series(dtype=float)

    with pytest.raises(Exception) as excinfo:
        _run_backtest(monkeypatch, _scores, params={"version": ""})
    assert "version is required" in str(excinfo.value)


def test_external_alpha_rebalance_skips_when_scores_empty(monkeypatch):
    def _empty(_as_of, _source, version=None, symbols=None):
        del version, symbols
        return pd.Series(dtype=float)

    result = _run_backtest(monkeypatch, _empty)

    assert result["totalExecutions"] == 0
    assert any("skip rebalance" in log for log in result["logs"])
