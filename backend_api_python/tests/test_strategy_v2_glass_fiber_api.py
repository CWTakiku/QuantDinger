"""Strategy V2 sandbox: get_glass_fiber_industry_week injection."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from app.services.strategy_v2 import contract
from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.runtime import (
    StrategyV2BacktestRunner,
    StrategyV2LiveSession,
    get_glass_fiber_industry_week,
)


def test_glass_fiber_industry_week_allowed_in_contract():
    assert "get_glass_fiber_industry_week" in contract._RUNTIME_GLOBAL_CALL_NAMES


def test_glass_fiber_industry_week_is_allowed_api_name():
    code = """
def initialize(context):
    context.set_universe(["CNStock:600176.SH"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    industry = get_glass_fiber_industry_week()
    log(str(industry.get("industry_available")))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_glass_fiber_industry_week_unavailable_payload():
    with patch(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        return_value=None,
    ):
        out = get_glass_fiber_industry_week(date(2026, 8, 1))
    assert out == {
        "industry_available": False,
        "as_of": None,
        "cloth_trend": 0,
        "inventory_trend": 0,
        "new_capacity_flag": 0,
        "source": None,
        "confidence": 0.0,
    }


def test_glass_fiber_industry_week_returns_resolve_row():
    row = {
        "industry_available": True,
        "as_of": "2026-08-01",
        "cloth_trend": -1,
        "inventory_trend": 1,
        "new_capacity_flag": 1,
        "source": "public_news",
        "confidence": 0.8,
    }
    with patch(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        return_value=row,
    ):
        out = get_glass_fiber_industry_week(date(2026, 8, 3))
    assert out is row


def test_glass_fiber_industry_week_bound_in_backtest_runner():
    code = """
def initialize(context):
    context.set_universe(["CNStock:600176.SH"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={
            "CNStock:600176.SH": pd.DataFrame(
                {
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.8],
                    "close": [10.2],
                    "volume": [1000.0],
                },
                index=pd.date_range("2026-08-01", periods=1),
            )
        },
        initial_capital=100_000.0,
    )
    assert callable(runner.program.namespace.get("get_glass_fiber_industry_week"))


def test_glass_fiber_industry_week_bound_in_live_session():
    code = """
def initialize(context):
    context.set_universe(["CNStock:600176.SH"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    session = StrategyV2LiveSession(
        code=code,
        frames={
            "CNStock:600176.SH": pd.DataFrame(
                {
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.8],
                    "close": [10.2],
                    "volume": [1000.0],
                },
                index=pd.date_range("2026-08-01", periods=1),
            )
        },
        initial_capital=100_000.0,
    )
    assert callable(session.program.namespace.get("get_glass_fiber_industry_week"))


def test_strategy_code_can_call_glass_fiber_api(monkeypatch):
    captured = {}

    def _fake_resolve(as_of):
        captured["as_of"] = as_of
        return {
            "industry_available": True,
            "as_of": "2026-08-01",
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "manual",
            "confidence": 1.0,
        }

    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        _fake_resolve,
    )
    code = """
def initialize(context):
    context.set_universe(["CNStock:600176.SH"])
    context.subscribe(frequency="1d")
    g.g_seen = False

def handle_data(context, data):
    industry = get_glass_fiber_industry_week(context.current_dt.date())
    g.g_seen = industry.get("industry_available")
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={
            "CNStock:600176.SH": pd.DataFrame(
                {
                    "open": [10.0],
                    "high": [10.5],
                    "low": [9.8],
                    "close": [10.2],
                    "volume": [1000.0],
                },
                index=pd.date_range("2026-08-03", periods=1),
            )
        },
        initial_capital=100_000.0,
    )
    result = runner.run()
    assert captured["as_of"] == date(2026, 8, 3)
    assert runner.program.state.g_seen is True
