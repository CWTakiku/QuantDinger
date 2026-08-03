from app.markets.cn_stock.board_lot import cn_stock_board_lot
from app.services.strategy_v2.runtime import MultiAssetSimulationBroker


def test_cn_stock_board_lot_rules():
    assert cn_stock_board_lot("CNStock:600519.SH") == 100.0
    assert cn_stock_board_lot("CNStock:000001.SZ@spot") == 100.0
    assert cn_stock_board_lot("CNStock:300750.SZ") == 100.0
    assert cn_stock_board_lot("CNStock:688981.SH@spot") == 200.0
    assert cn_stock_board_lot("CNStock:689009.SH") == 200.0
    assert cn_stock_board_lot("Crypto:BTC/USDT") is None
    assert cn_stock_board_lot("USStock:AAPL") is None


def test_strategy_broker_uses_cn_board_lot_when_bar_omits_lot_size():
    assert MultiAssetSimulationBroker._lot_size("CNStock:600519.SH@spot", {}) == 100.0
    assert MultiAssetSimulationBroker._lot_size("CNStock:688981.SH@spot", {}) == 200.0
    assert MultiAssetSimulationBroker._lot_size("CNStock:600519.SH@spot", {"lot_size": 50}) == 50.0
    assert MultiAssetSimulationBroker._lot_size("Crypto:BTC/USDT", {}) == 1e-8
    assert MultiAssetSimulationBroker._lot_size("USStock:AAPL", {}) == 1.0
