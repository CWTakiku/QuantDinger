from app.services.strategy_v2.contract import compile_strategy_v2
from app.services.strategy_v2.service import _benchmark_for_manifest


def test_csi300_pool_defaults_to_csi300_index_benchmark():
    code = '''
def initialize(context):
    context.set_universe(pool="csi300")
    context.subscribe(frequency="1d")
    context.set_warmup(5)
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    pass
'''
    manifest = compile_strategy_v2(code)
    bench = _benchmark_for_manifest(manifest)
    assert bench is not None
    assert bench.market == "CNStock"
    assert bench.symbol == "000300.SH"


def test_csi500_pool_defaults_to_csi500_index_benchmark():
    code = '''
def initialize(context):
    context.set_universe(pool="csi500")
    context.subscribe(frequency="1d")
    context.set_warmup(5)
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    pass
'''
    manifest = compile_strategy_v2(code)
    bench = _benchmark_for_manifest(manifest)
    assert bench is not None
    assert bench.symbol == "000905.SH"


def test_explicit_benchmark_wins_over_pool_default():
    code = '''
def initialize(context):
    context.set_universe(pool="csi300")
    context.set_benchmark("CNStock:000905.SH")
    context.subscribe(frequency="1d")
    context.set_warmup(5)
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    pass
'''
    manifest = compile_strategy_v2(code)
    bench = _benchmark_for_manifest(manifest)
    assert bench.symbol == "000905.SH"
