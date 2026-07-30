from unittest.mock import patch
from app.data_sources.cn_stock import CNStockDataSource


def test_get_kline_prefers_tushare_for_daily(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    rows = [{"time": 1736035200, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    src = CNStockDataSource()
    with patch("app.data_sources.cn_stock.fetch_tushare_daily_klines", return_value=rows) as mocked, \
         patch("app.data_sources.cn_stock.fetch_twelvedata_klines", return_value=[]) as twelve:
        out = src.get_kline("600519.SH", "1d", 10)
    assert out == rows or out[0]["close"] == 1.5
    mocked.assert_called()
    twelve.assert_not_called()
