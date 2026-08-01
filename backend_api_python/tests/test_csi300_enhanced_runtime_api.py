from pathlib import Path

from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.runtime import StrategyV2BacktestRunner


def test_optimize_enhanced_index_partial_is_allowed_api_name():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_benchmark("CNStock:000300.SH")

def handle_data(context, data):
    result = optimize_enhanced_index_partial(
        {"CNStock:600519.SH": 1.0},
        {"CNStock:600519.SH": 1.0},
        {"CNStock:600519.SH": 1.0},
        ["CNStock:600519.SH"],
    )
    log(str(result.get("status")))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_optimize_enhanced_index_is_allowed_api_name():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_benchmark("CNStock:000300.SH")

def handle_data(context, data):
    result = optimize_enhanced_index({"CNStock:600519.SH": 1.0}, {"CNStock:600519.SH": 1.0})
    log(str(result.get("status")))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_csi300_enhanced_readonly_helpers_are_allowed_api_names():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_benchmark("CNStock:000300.SH")

def handle_data(context, data):
    as_of = str(context.current_dt.date())
    symbols = ["CNStock:600519.SH", "CNStock:000858.SZ"]
    bench = get_csi300_bench_weights(as_of, symbols=symbols)
    bench2, source = get_csi300_bench_weights_with_meta(as_of, symbols=symbols)
    industry = get_ashare_industry_map(symbols, as_of)
    size = get_ashare_size_log_mcap(symbols, as_of)
    log(str(len(bench)) + str(len(bench2)) + str(source) + str(len(industry)) + str(len(size)))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_csi300_enhanced_readonly_helpers_are_bound_at_runtime():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600519.SH": __import__("pandas").DataFrame({
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }, index=__import__("pandas").date_range("2026-07-31", periods=1))},
        initial_capital=100000.0,
    )
    namespace = runner.program.namespace
    for name in (
        "get_csi300_bench_weights",
        "get_csi300_bench_weights_with_meta",
        "get_ashare_industry_map",
        "get_ashare_size_log_mcap",
    ):
        assert callable(namespace.get(name)), name


def test_csi300_enhanced_icir_regime_helpers_are_allowed_api_names():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_benchmark("CNStock:000300.SH")

def handle_data(context, data):
    w = apply_regime({"momentum": 0.5, "risk_liq": 0.5}, bench_ret_20=-0.1, threshold=-0.08)
    log(str(w.get("momentum")))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_csi300_enhanced_icir_regime_helpers_are_bound_at_runtime():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600519.SH": __import__("pandas").DataFrame({
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }, index=__import__("pandas").date_range("2026-07-31", periods=1))},
        initial_capital=100000.0,
    )
    namespace = runner.program.namespace
    for name in ("apply_icir_weights", "apply_regime"):
        assert callable(namespace.get(name)), name


def test_csi300_enhanced_flow_consensus_helpers_are_allowed_api_names():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_benchmark("CNStock:000300.SH")

def handle_data(context, data):
    as_of = str(context.current_dt.date())
    symbols = ["CNStock:600519.SH", "CNStock:000858.SZ"]
    flow = get_ashare_flow_panel(symbols, as_of)
    consensus = get_ashare_consensus_panel(symbols, as_of)
    log(str(len(flow)) + str(len(consensus)))
"""
    compiled = compile_strategy_v2(code)
    assert callable(compiled.handler("handle_data"))


def test_csi300_enhanced_flow_consensus_helpers_are_bound_at_runtime():
    code = """
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""
    runner = StrategyV2BacktestRunner(
        code=code,
        frames={"CNStock:600519.SH": __import__("pandas").DataFrame({
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }, index=__import__("pandas").date_range("2026-07-31", periods=1))},
        initial_capital=100000.0,
    )
    namespace = runner.program.namespace
    for name in ("get_ashare_flow_panel", "get_ashare_consensus_panel"):
        assert callable(namespace.get(name)), name


def test_example_csi300_enhanced_weekly_compiles():
    path = Path(__file__).resolve().parents[2] / "docs" / "examples" / "strategy_v2_csi300_enhanced_weekly.py"
    code = path.read_text(encoding="utf-8")
    compiled = compile_strategy_v2(code)
    assert "csi300" in str(compiled.manifest.universe.reference).lower()
    assert callable(compiled.handler("update_alpha"))
    assert callable(compiled.handler("rebalance"))
    assert "_eligible_symbols" in code
    assert "min_history_bars" in code
    assert int(compiled.manifest.warmup_bars or 0) >= 140


def test_example_csi300_enhanced_v2_weekly_compiles():
    path = Path(__file__).resolve().parents[2] / "docs" / "examples" / "strategy_v2_csi300_enhanced_v2_weekly.py"
    code = path.read_text(encoding="utf-8")
    assert code.lstrip().startswith('"""CSI300 Enhanced Index QP 2.0')
    compiled = compile_strategy_v2(code)
    assert "csi300" in str(compiled.manifest.universe.reference).lower()
    assert callable(compiled.handler("update_alpha"))
    assert callable(compiled.handler("rebalance"))
    assert "get_csi300_bench_weights" in code
    assert "get_csi300_bench_weights_with_meta" in code
    assert "get_ashare_industry_map" in code
    assert "get_ashare_size_log_mcap" in code
    assert "get_ashare_flow_panel" in code
    assert "get_ashare_consensus_panel" in code
    assert "industry_limit" in code
    assert "te_limit" in code
    assert "(252.0 ** 0.5)" in code or "sqrt(252)" in code
    assert "use_icir" in code
    assert "regime_enabled" in code
    assert "apply_regime" in code
    assert "optimize_enhanced_index" in code
    assert "optimize_enhanced_index_partial" in code
    assert "monitor_partial_rebalance" in code
    assert "record_enhanced_index_diagnostics" in code
    assert "partial_rebalance_enabled" in code
    assert int(compiled.manifest.warmup_bars or 0) >= 140
