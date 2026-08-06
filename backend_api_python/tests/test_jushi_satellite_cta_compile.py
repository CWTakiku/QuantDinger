"""Compile / param extraction tests for Jushi satellite CTA example."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.services.indicator_params import IndicatorParamsParser
from app.services.strategy_v2 import StrategyV2BacktestRunner, compile_strategy_v2

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "docs/examples/strategy_v2_jushi_satellite_cta.py"
)
CLI_PATH = Path(__file__).resolve().parents[1] / "scripts/upsert_glass_fiber_industry_week.py"

EXPECTED_PARAMS = (
    "satellite_max_pct",
    "hard_stop_pct",
    "t_max_frac",
    "min_amount",
    "liquidity_lookback",
    "enable_intraday_t",
    "market_stress",
    "gm_qoq",
    "cash_profit_ratio",
    "pe_percentile",
    "pe_exit_percentile",
)


def _example_code() -> str:
    assert EXAMPLE_PATH.is_file(), f"missing example: {EXAMPLE_PATH}"
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def _load_jushi_pure_helpers():
    code = _example_code()
    start = code.index("def _industry_downturn")
    end = code.index("def _resolve_pe_percentile")
    ns: dict = {}
    exec(code[start:end], ns)
    return ns


def _good_industry():
    return {
        "industry_available": True,
        "as_of": "2026-08-01",
        "cloth_trend": 1,
        "inventory_trend": -1,
        "new_capacity_flag": 0,
        "source": "manual",
        "confidence": 1.0,
    }


def _jushi_backtest_frame(*, close: float, volume: float, periods: int = 90):
    return pd.DataFrame(
        {
            "open": [close] * periods,
            "high": [close * 1.01] * periods,
            "low": [close * 0.99] * periods,
            "close": [close] * periods,
            "volume": [volume] * periods,
        },
        index=pd.date_range("2026-05-01", periods=periods, freq="D"),
    )


def test_jushi_avg_daily_amount_uses_close_times_volume():
    code = _example_code()
    start = code.index("def _avg_daily_amount")
    end = code.index("def _resolve_pe_percentile")

    def _history(count, _freq, fields, symbol):
        assert symbol == "CNStock:600176.SH"
        assert count == 2
        return pd.DataFrame({"close": [10.0, 12.0], "volume": [100.0, 200.0]})

    ns: dict = {"get_history": _history}
    exec(code[start:end], ns)
    avg_daily_amount = ns["_avg_daily_amount"]
    assert avg_daily_amount("CNStock:600176.SH", 2) == 1700.0

    ns["get_history"] = lambda *_a, **_k: None
    assert avg_daily_amount("CNStock:600176.SH", 5) == 0.0


def test_jushi_satellite_cta_opens_when_liquidity_ok(monkeypatch):
    code = _example_code()
    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        lambda *_a, **_k: _good_industry(),
    )
    frame = _jushi_backtest_frame(close=10.0, volume=500_000_000.0)
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600176.SH": frame, "CNStock:000300.SH": frame},
        initial_capital=100_000.0,
        params={"min_amount": 4_000_000_000.0, "liquidity_lookback": 5},
        commission=0,
        slippage=0,
    ).run()
    reasons = {item.get("reason") for item in result.get("orderLedger") or []}
    assert "jushi_satellite_target" in reasons
    last = (result.get("holdingSnapshots") or [{}])[-1]
    positions = last.get("positions") or {}
    assert "CNStock:600176.SH" in positions
    assert float(positions["CNStock:600176.SH"]["quantity"]) > 0


def test_jushi_satellite_cta_refuses_open_when_liquidity_low(monkeypatch):
    code = _example_code()
    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        lambda *_a, **_k: _good_industry(),
    )
    frame = _jushi_backtest_frame(close=10.0, volume=1_000_000.0)
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600176.SH": frame, "CNStock:000300.SH": frame},
        initial_capital=100_000.0,
        params={"min_amount": 4_000_000_000.0, "liquidity_lookback": 5},
        commission=0,
        slippage=0,
    ).run()
    reasons = {item.get("reason") for item in result.get("orderLedger") or []}
    assert "jushi_satellite_target" in reasons
    last = (result.get("holdingSnapshots") or [{}])[-1]
    assert not (last.get("positions") or {})


def test_jushi_satellite_cta_example_compiles():
    manifest = compile_strategy_v2(_example_code()).manifest
    assert manifest.strategy_type == "cta"
    assert manifest.direction_mode == "long_only"
    assert manifest.primary_frequency == "1d"
    assert len(manifest.universe.instruments) == 1
    assert manifest.universe.instruments[0].key == "CNStock:600176.SH"


def test_jushi_satellite_cta_params_declared_and_used():
    code = _example_code()
    declared = {item["name"]: item for item in IndicatorParamsParser.parse_params(code)}
    assert set(EXPECTED_PARAMS).issubset(declared.keys())
    for name in EXPECTED_PARAMS:
        assert f'# @param {name} ' in code
        assert (
            f'context.params.get("{name}"' in code
            or f'params.get("{name}"' in code
        ), name
    assert declared["satellite_max_pct"]["default"] == 0.20
    assert declared["hard_stop_pct"]["default"] == 0.12


def test_jushi_satellite_cta_tracks_last_industry_for_consecutive_downturn():
    code = _example_code()
    assert "g.last_industry" in code
    assert "jushi_industry_downturn" in code
    assert "_should_record_industry_week" in code
    assert "_is_two_week_downturn" in code


def test_jushi_should_record_industry_week_only_on_as_of_change():
    h = _load_jushi_pure_helpers()
    should_record = h["_should_record_industry_week"]
    prev = {
        "industry_available": True,
        "cloth_trend": -1,
        "inventory_trend": 1,
        "new_capacity_flag": 0,
        "as_of": "2026-08-01",
    }
    assert should_record(None, "2026-08-01") is True
    assert should_record(prev, "2026-08-01") is False
    assert should_record(prev, "2026-08-08") is True
    assert should_record(prev, None) is False


def test_jushi_is_two_week_downturn_requires_distinct_weeks():
    h = _load_jushi_pure_helpers()
    is_two_week = h["_is_two_week_downturn"]
    downturn = {
        "industry_available": True,
        "cloth_trend": -1,
        "inventory_trend": 1,
        "new_capacity_flag": 0,
        "as_of": "2026-08-01",
    }
    curr = dict(downturn, as_of="2026-08-08")
    assert is_two_week(downturn, curr) is True
    assert is_two_week(downturn, downturn) is False
    assert is_two_week(
        downturn,
        dict(downturn, cloth_trend=1, as_of="2026-08-08"),
    ) is False
    assert is_two_week(None, curr) is False


def test_jushi_maybe_record_industry_week_skips_same_as_of():
    h = _load_jushi_pure_helpers()
    maybe_record = h["_maybe_record_industry_week"]
    prev = {
        "industry_available": True,
        "cloth_trend": -1,
        "inventory_trend": 1,
        "new_capacity_flag": 0,
        "as_of": "2026-08-01",
    }
    same_week = {
        "industry_available": True,
        "cloth_trend": 1,
        "inventory_trend": -1,
        "new_capacity_flag": 0,
        "as_of": "2026-08-01",
    }
    assert maybe_record(prev, same_week) is prev
    new_week = dict(same_week, as_of="2026-08-08")
    recorded = maybe_record(prev, new_week)
    assert recorded is not prev
    assert recorded["as_of"] == "2026-08-08"
    assert recorded["cloth_trend"] == 1


def test_jushi_satellite_cta_no_industry_blocks_new_open(monkeypatch):
    code = _example_code()

    def _unavailable(*_args, **_kwargs):
        return {
            "industry_available": False,
            "as_of": None,
            "cloth_trend": 0,
            "inventory_trend": 0,
            "new_capacity_flag": 0,
            "source": None,
            "confidence": 0.0,
        }

    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store.resolve_glass_fiber_week",
        lambda *_a, **_k: None,
    )

    frame = pd.DataFrame(
        {
            "open": [10.0] * 90,
            "high": [10.5] * 90,
            "low": [9.8] * 90,
            "close": [10.2] * 90,
            "volume": [50_000_000.0] * 90,
        },
        index=pd.date_range("2026-05-01", periods=90, freq="D"),
    )
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600176.SH": frame, "CNStock:000300.SH": frame},
        initial_capital=100_000.0,
    )
    result = runner.run()
    orders = result.get("orders") or []
    assert not orders, "no industry => no satellite opens"


def test_upsert_glass_fiber_cli_argparse_smoke(monkeypatch, capsys):
    captured: dict = {}

    def _fake_upsert(row):
        captured["row"] = row
        return {
            "as_of": "2026-08-01",
            "cloth_trend": row["cloth_trend"],
            "inventory_trend": row["inventory_trend"],
            "new_capacity_flag": row.get("new_capacity_flag", 0),
            "source": row["source"],
            "confidence": row.get("confidence", 1.0),
        }

    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store.upsert_glass_fiber_week",
        _fake_upsert,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "upsert_glass_fiber_industry_week.py",
            "--as-of",
            "2026-08-01",
            "--cloth-trend",
            "1",
            "--inventory-trend",
            "-1",
            "--source",
            "manual",
        ],
    )

    script_dir = str(CLI_PATH.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import importlib

    module = importlib.import_module("upsert_glass_fiber_industry_week")
    importlib.reload(module)
    assert module.main() == 0
    row = captured["row"]
    assert row["as_of"] == "2026-08-01"
    assert row["cloth_trend"] == 1
    assert row["inventory_trend"] == -1
    assert row["source"] == "manual"
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "manual"
