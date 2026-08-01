import pandas as pd

from app.services.csi300_enhanced.diagnostics import (
    build_enhanced_index_diagnostics,
    summarize_enhanced_index_diagnostics,
)
from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.runtime import StrategyV2BacktestRunner


def test_build_enhanced_index_diagnostics_payload():
    payload = build_enhanced_index_diagnostics(
        as_of="2026-07-31",
        weights={"A": 0.55, "B": 0.45},
        w_bench={"A": 0.5, "B": 0.5},
        optimize_result={"status": "optimal", "turnover": 0.04, "active_risk_proxy": 0.03},
        bench_source="csi300_pit",
        industry={"A": "IND1", "B": "IND2"},
        size_z={"A": 1.0, "B": -1.0},
        kind="weekly",
    )
    assert payload["benchSource"] == "csi300_pit"
    assert payload["benchFallback"] is False
    assert payload["teExante"] == payload["activeRiskProxy"] == 0.03
    assert abs(payload["sizeExposure"] - 0.1) < 1e-9
    assert abs(payload["industryActiveDeviation"]["maxAbs"] - 0.05) < 1e-9
    assert abs(payload["industryActiveDeviation"]["byIndustry"]["IND1"] - 0.05) < 1e-9


def test_build_enhanced_index_diagnostics_marks_bench_fallback():
    payload = build_enhanced_index_diagnostics(
        as_of="2026-07-31",
        weights={"A": 0.6, "B": 0.4},
        w_bench={"A": 0.5, "B": 0.5},
        optimize_result={"status": "optimal", "turnover": 0.0, "active_risk_proxy": 0.01},
        bench_source="equal_fallback",
        kind="weekly",
    )
    assert payload["benchFallback"] is True
    assert payload["benchSource"] == "equal_fallback"


def test_summarize_enhanced_index_diagnostics():
    records = [
        build_enhanced_index_diagnostics(
            as_of="2026-07-01",
            weights={"A": 0.6, "B": 0.4},
            w_bench={"A": 0.5, "B": 0.5},
            optimize_result={"status": "optimal", "turnover": 0.02, "active_risk_proxy": 0.02},
            bench_source="equal_fallback",
            kind="weekly",
        ),
        build_enhanced_index_diagnostics(
            as_of="2026-07-08",
            weights={"A": 0.55, "B": 0.45},
            w_bench={"A": 0.5, "B": 0.5},
            optimize_result={"status": "optimal", "turnover": 0.01, "active_risk_proxy": 0.04},
            bench_source="csi300_pit",
            kind="weekly",
        ),
    ]
    summary = summarize_enhanced_index_diagnostics(records)
    assert summary["rebalanceCount"] == 2
    assert summary["benchFallbackRate"] == 0.5
    assert summary["maxTeExante"] == 0.04
    assert summary["last"]["asOf"] == "2026-07-08"


def _frame(days: int = 30) -> pd.DataFrame:
    index = pd.date_range("2026-07-01", periods=days, freq="B")
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(days)],
            "high": [101.0 + i for i in range(days)],
            "low": [99.0 + i for i in range(days)],
            "close": [100.5 + i for i in range(days)],
            "volume": [1_000_000.0] * days,
        },
        index=index,
    )


def test_v2_backtest_result_includes_enhanced_index_diagnostics():
    code = """
def initialize(context):
    context.set_universe(["CNStock:A.SH", "CNStock:B.SH", "CNStock:C.SH"])
    context.subscribe(frequency="1d")
    context.set_warmup(1)
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    as_of = str(context.current_dt.date())
    weights = {"CNStock:A.SH": 0.4, "CNStock:B.SH": 0.35, "CNStock:C.SH": 0.25}
    w_bench = {"CNStock:A.SH": 0.34, "CNStock:B.SH": 0.33, "CNStock:C.SH": 0.33}
    result = {"status": "optimal", "turnover": 0.05, "active_risk_proxy": 0.025}
    record_enhanced_index_diagnostics(
        as_of=as_of,
        weights=weights,
        w_bench=w_bench,
        optimize_result=result,
        bench_source="csi300_pit",
        industry={"CNStock:A.SH": "IND1", "CNStock:B.SH": "IND1", "CNStock:C.SH": "IND2"},
        size_z={"CNStock:A.SH": 1.0, "CNStock:B.SH": 0.0, "CNStock:C.SH": -1.0},
        kind="weekly",
    )
    for symbol, weight in weights.items():
        order_target_percent(symbol, weight)
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("rebalance"))

    frames = {
        "CNStock:A.SH": _frame(),
        "CNStock:B.SH": _frame(),
        "CNStock:C.SH": _frame(),
    }
    runner = StrategyV2BacktestRunner(code=code, frames=frames, initial_capital=1_000_000.0)
    result = runner.run()
    diag = (result.get("diagnostics") or {}).get("enhancedIndex") or {}
    assert diag.get("rebalanceCount", 0) >= 1
    last = diag.get("last") or {}
    for key in (
        "benchSource",
        "benchFallback",
        "teExante",
        "activeRiskProxy",
        "sizeExposure",
        "industryActiveDeviation",
    ):
        assert key in last, key
