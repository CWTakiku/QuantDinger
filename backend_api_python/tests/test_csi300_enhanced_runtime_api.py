from pathlib import Path

from app.services.strategy_v2.contract import compile_strategy_v2


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
