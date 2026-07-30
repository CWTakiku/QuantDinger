from unittest.mock import MagicMock, patch

import pandas as pd

from app.data_sources import tushare_cn
from app.data_sources.tushare_cn import _build_pro, fetch_tushare_daily_klines, tencent_code_to_ts_code


def test_tencent_code_to_ts_code():
    assert tencent_code_to_ts_code("SH600519") == "600519.SH"
    assert tencent_code_to_ts_code("SZ000001") == "000001.SZ"
    assert tencent_code_to_ts_code("600519.SH") == "600519.SH"
    assert tencent_code_to_ts_code("000001") == "000001.SZ"


def test_build_pro_uses_token_and_custom_http_url(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://example.test/api")
    tushare_cn._pro_cache.clear()

    fake_pro = MagicMock()
    with patch("tushare.pro_api", return_value=fake_pro) as pro_api:
        first = _build_pro()
        second = _build_pro()

    pro_api.assert_called_once_with("dummy-token")
    assert first is second
    assert fake_pro._DataApi__http_url == "https://example.test/api"


def test_fetch_tushare_daily_klines_maps_rows(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://example.test/api")

    frame = pd.DataFrame(
        [
            {"trade_date": "20260105", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "vol": 1000.0},
            {"trade_date": "20260106", "open": 10.5, "high": 11.2, "low": 10.0, "close": 11.0, "vol": 1200.0},
        ]
    )
    fake_pro = MagicMock()
    fake_pro.daily.return_value = frame

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SH600519", limit=2)

    assert len(rows) == 2
    assert rows[0]["open"] == 10.0
    assert rows[-1]["close"] == 11.0
    assert "time" in rows[0]
    fake_pro.daily.assert_called()
    kwargs = fake_pro.daily.call_args.kwargs
    assert kwargs["ts_code"] == "600519.SH"
