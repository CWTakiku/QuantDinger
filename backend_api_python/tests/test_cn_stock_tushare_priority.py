from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.data_sources.cn_stock import CNStockDataSource


def _today_shanghai_midnight_unix() -> int:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return int(
        datetime(today.year, today.month, today.day, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    )


def test_get_kline_prefers_tushare_for_daily_when_fresh(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    rows = [{"time": _today_shanghai_midnight_unix(), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    src = CNStockDataSource()
    with patch("app.data_sources.cn_stock.fetch_tushare_daily_klines", return_value=rows) as mocked, \
         patch("app.data_sources.cn_stock.fetch_twelvedata_klines", return_value=[]) as twelve:
        out = src.get_kline("600519.SH", "1d", 10)
    assert out[0]["close"] == 1.5
    mocked.assert_called()
    twelve.assert_not_called()


def test_get_kline_falls_through_when_tushare_stale(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    stale = [{"time": _today_shanghai_midnight_unix() - 86400, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    fresh = [{"time": _today_shanghai_midnight_unix(), "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 20}]
    src = CNStockDataSource()
    with patch("app.data_sources.cn_stock.fetch_tushare_daily_klines", return_value=stale), \
         patch("app.data_sources.cn_stock.fetch_twelvedata_klines", return_value=[]), \
         patch("app.data_sources.cn_stock.fetch_kline", return_value=[[]]), \
         patch("app.data_sources.cn_stock.tencent_kline_rows_to_dicts", return_value=fresh), \
         patch("app.data_sources.cn_stock.fetch_yfinance_klines", return_value=[]), \
         patch("app.data_sources.cn_stock.fetch_akshare_minute_klines", return_value=[]), \
         patch("app.data_sources.cn_stock.fetch_akshare_weekly_klines", return_value=[]):
        out = src.get_kline("600519.SH", "1d", 10)
    assert out[0]["close"] == 2.5
