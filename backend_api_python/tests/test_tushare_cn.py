from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.data_sources import tushare_cn
from app.data_sources.tushare_cn import (
    _build_pro,
    _trade_date_to_unix,
    daily_bars_cover_today,
    fetch_tushare_daily_klines,
    is_ashare_index_ts_code,
    tencent_code_to_ts_code,
)


@pytest.fixture(autouse=True)
def _fresh_market_caches(monkeypatch):
    monkeypatch.setattr(tushare_cn, "_market_flags", {})
    monkeypatch.setattr(tushare_cn, "_market_flag_failures", {})
    monkeypatch.setattr(tushare_cn, "_market_days", {})
    monkeypatch.setattr(tushare_cn, "_market_day_failures", {})


def test_tencent_code_to_ts_code():
    assert tencent_code_to_ts_code("SH600519") == "600519.SH"
    assert tencent_code_to_ts_code("SZ000001") == "000001.SZ"
    assert tencent_code_to_ts_code("600519.SH") == "600519.SH"
    assert tencent_code_to_ts_code("000001") == "000001.SZ"


def test_is_ashare_index_ts_code():
    assert is_ashare_index_ts_code("000300.SH") is True
    assert is_ashare_index_ts_code("000905.SH") is True
    assert is_ashare_index_ts_code("399006.SZ") is True
    assert is_ashare_index_ts_code("000001.SZ") is False
    assert is_ashare_index_ts_code("600519.SH") is False


def test_trade_date_maps_to_shanghai_midnight():
    ts = _trade_date_to_unix("20260729")
    local = datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Shanghai"))
    assert local.year == 2026 and local.month == 7 and local.day == 29
    assert local.hour == 0 and local.minute == 0


def test_daily_bars_cover_today(monkeypatch):
    sh = ZoneInfo("Asia/Shanghai")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 15, 30, tzinfo=sh)

    monkeypatch.setattr(tushare_cn, "datetime", _FrozenDatetime)
    today_ts = int(datetime(2026, 10, 9, tzinfo=sh).timestamp())
    assert daily_bars_cover_today([{"time": today_ts}]) is True
    assert daily_bars_cover_today([{"time": today_ts - 86400}]) is False
    assert daily_bars_cover_today([]) is False


def test_build_pro_uses_token_and_custom_http_url(monkeypatch):
    monkeypatch.delenv("TUSHARE_API_KEY", raising=False)
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
    local = datetime.fromtimestamp(rows[0]["time"], tz=ZoneInfo("Asia/Shanghai"))
    assert local.hour == 0
    fake_pro.daily.assert_called()
    kwargs = fake_pro.daily.call_args.kwargs
    assert kwargs["ts_code"] == "600519.SH"


def test_fetch_tushare_daily_klines_uses_index_daily_for_csi300(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")

    frame = pd.DataFrame(
        [
            {"trade_date": "20260105", "open": 3000.0, "high": 3010.0, "low": 2990.0, "close": 3005.0, "vol": 1.0},
            {"trade_date": "20260106", "open": 3005.0, "high": 3020.0, "low": 3000.0, "close": 3015.0, "vol": 1.0},
        ]
    )
    fake_pro = MagicMock()
    fake_pro.index_daily.return_value = frame
    fake_pro.daily.return_value = pd.DataFrame()

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SH000300", limit=2)

    assert len(rows) == 2
    assert rows[-1]["close"] == 3015.0
    fake_pro.index_daily.assert_called()
    fake_pro.daily.assert_not_called()
    assert fake_pro.index_daily.call_args.kwargs["ts_code"] == "000300.SH"


def test_fetch_tushare_daily_klines_splits_long_ranges(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    calls = []

    def _daily(**kwargs):
        calls.append((kwargs["start_date"], kwargs["end_date"]))
        return pd.DataFrame(
            [{"trade_date": kwargs["end_date"], "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1.0}]
        )

    fake_pro = MagicMock()
    fake_pro.daily.side_effect = _daily
    for name in ("adj_factor", "stk_limit", "suspend_d", "stock_st"):
        getattr(fake_pro, name).return_value = pd.DataFrame()
    monkeypatch.setattr(tushare_cn, "_market_flags", {})
    monkeypatch.setattr(tushare_cn, "_market_flag_failures", {})

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SZ000001", limit=217, before_time=1791388800)

    assert len(calls) >= 3
    for start, end in calls:
        span = (datetime.strptime(end, "%Y%m%d") - datetime.strptime(start, "%Y%m%d")).days
        assert span < 180
    assert calls[0][0] < calls[-1][0]
    assert rows


def _market_daily(trade_date=None, **_):
    if trade_date not in {"20260929", "20260930"}:
        return pd.DataFrame()
    base = 10.0 if trade_date == "20260929" else 11.0
    return pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "trade_date": trade_date, "open": base, "high": base,
             "low": base, "close": base, "pre_close": base, "vol": 100.0},
            {"ts_code": "600519.SH", "trade_date": trade_date, "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "pre_close": 1.0, "vol": 100.0},
        ]
    )


def _market_adj(trade_date=None, **_):
    if trade_date not in {"20260929", "20260930"}:
        return pd.DataFrame()
    factor = 1.0 if trade_date == "20260929" else 2.0
    return pd.DataFrame(
        [{"ts_code": "000001.SZ", "trade_date": trade_date, "adj_factor": factor},
         {"ts_code": "600519.SH", "trade_date": trade_date, "adj_factor": 1.0}]
    )


def _snapshot_pro():
    fake_pro = MagicMock()
    fake_pro.daily.side_effect = _market_daily
    fake_pro.adj_factor.side_effect = _market_adj
    for name in ("stk_limit", "suspend_d", "stock_st"):
        getattr(fake_pro, name).return_value = pd.DataFrame()
    return fake_pro


def test_short_window_uses_cached_market_snapshots(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    fake_pro = _snapshot_pro()
    after = int(datetime(2026, 9, 20, tzinfo=ZoneInfo("UTC")).timestamp())
    before = int(datetime(2026, 10, 1, tzinfo=ZoneInfo("UTC")).timestamp())

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SZ000001", limit=217, before_time=before, after_time=after)
        calls = fake_pro.daily.call_count
        other = fetch_tushare_daily_klines(tencent_code="SH600519", limit=217, before_time=before, after_time=after)

    assert [r["close"] for r in rows] == [5.0, 11.0]
    assert len(other) == 2
    assert all("ts_code" not in c.kwargs for c in fake_pro.daily.call_args_list)
    assert fake_pro.daily.call_count == calls
    first_day = min(c.kwargs["trade_date"] for c in fake_pro.daily.call_args_list)
    assert first_day == "20260921"


def test_failed_market_snapshot_falls_back_to_symbol_request(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    fake_pro = _snapshot_pro()

    def _daily(trade_date=None, **kwargs):
        if trade_date:
            raise RuntimeError("HTTP 503 upstream_pool_exhausted")
        return pd.DataFrame(
            [{"trade_date": "20260930", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1.0}]
        )

    fake_pro.daily.side_effect = _daily
    after = int(datetime(2026, 9, 20, tzinfo=ZoneInfo("UTC")).timestamp())
    before = int(datetime(2026, 10, 1, tzinfo=ZoneInfo("UTC")).timestamp())

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SZ000001", limit=217, before_time=before, after_time=after)

    assert len(rows) == 1
    assert fake_pro.daily.call_args.kwargs["ts_code"] == "000001.SZ"
    assert fake_pro.daily.call_args.kwargs["start_date"] == "20260919"


def test_fetch_tushare_daily_klines_applies_qfq_limits_and_suspension(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    prices = pd.DataFrame(
        [
            {
                "trade_date": "20260105",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "vol": 100.0,
                "pre_close": 9.5,
            },
            {
                "trade_date": "20260106",
                "open": 20.0,
                "high": 22.0,
                "low": 19.0,
                "close": 22.0,
                "vol": 0.0,
                "pre_close": 20.0,
            },
        ]
    )
    fake_pro = MagicMock()
    fake_pro.daily.return_value = prices
    fake_pro.adj_factor.return_value = pd.DataFrame(
        [
            {"trade_date": "20260105", "adj_factor": 1.0},
            {"trade_date": "20260106", "adj_factor": 2.0},
        ]
    )
    def _limits(trade_date):
        if trade_date == "20260106":
            return pd.DataFrame([{"ts_code": "600519.SH", "up_limit": 22.0, "down_limit": 18.0}])
        return pd.DataFrame(columns=["ts_code", "up_limit", "down_limit"])

    def _suspends(trade_date):
        if trade_date == "20260105":
            return pd.DataFrame([{"ts_code": "600519.SH", "suspend_type": "S"}])
        return pd.DataFrame(columns=["ts_code", "suspend_type"])

    fake_pro.stk_limit.side_effect = _limits
    fake_pro.suspend_d.side_effect = _suspends
    fake_pro.stock_st.return_value = pd.DataFrame(columns=["ts_code"])
    monkeypatch.setattr(tushare_cn, "_market_flags", {})
    monkeypatch.setattr(tushare_cn, "_market_flag_failures", {})

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SH600519", limit=2)

    assert rows[0]["close"] == 5.0
    assert rows[1]["close"] == 22.0
    assert rows[0]["suspended"] is True
    assert rows[0]["limit_up"] is False
    assert rows[1]["limit_up"] is True
    assert rows[1]["suspended"] is True
    fake_pro.pro_bar.assert_not_called()
    assert fake_pro.stk_limit.call_count == 2

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        fetch_tushare_daily_klines(tencent_code="SZ000001", limit=2)
    assert fake_pro.stk_limit.call_count == 2
