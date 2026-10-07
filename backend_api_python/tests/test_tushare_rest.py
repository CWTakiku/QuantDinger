from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import requests

from app.data_sources import tushare_cn
from app.data_sources.tushare_rest import TushareRestError, TushareRestPro


class _Resp:
    def __init__(self, status: int, payload, reason: str = ""):
        self.status_code = status
        self._payload = payload
        self.reason = reason

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(fields, items):
    return _Resp(200, {"code": 0, "msg": "ok", "data": {"fields": fields, "items": items}})


def _client(session, **kw):
    return TushareRestPro(
        "k-123",
        base_url="https://primary/tushare/pro",
        backup_url="http://backup/tushare",
        session=session,
        **kw,
    )


def test_attribute_call_builds_get_with_api_key_and_returns_frame():
    session = _Session([_ok(["ts_code", "trade_date", "close"], [["000001.SZ", "20260930", 11.57]])])
    pro = _client(session)

    df = pro.daily(ts_code="000001.SZ", start_date="20260901", end_date=None, fields="ts_code,trade_date,close")

    call = session.calls[0]
    assert call["url"] == "https://primary/tushare/pro/daily"
    assert call["headers"] == {"X-API-Key": "k-123"}
    assert call["params"] == {
        "ts_code": "000001.SZ",
        "start_date": "20260901",
        "fields": "ts_code,trade_date,close",
    }
    assert list(df.columns) == ["ts_code", "trade_date", "close"]
    assert df.iloc[0]["close"] == pytest.approx(11.57)


def test_transient_error_retries_then_falls_back_to_dashed_backup(monkeypatch):
    monkeypatch.setattr("app.data_sources.tushare_rest.time.sleep", lambda *_: None)
    session = _Session([
        _Resp(503, {"ok": False, "error": "upstream_pool_exhausted"}),
        requests.Timeout("slow"),
        _ok(["exchange", "cal_date", "is_open"], [["SSE", "20261008", 1]]),
    ])
    pro = _client(session)

    df = pro.trade_cal(exchange="SSE", start_date="20261008", end_date="20261008")

    assert [c["url"] for c in session.calls] == [
        "https://primary/tushare/pro/trade_cal",
        "https://primary/tushare/pro/trade_cal",
        "http://backup/tushare/trade-cal",
    ]
    assert int(df.iloc[0]["is_open"]) == 1


def test_business_error_skips_retry_and_raises_when_backup_also_fails():
    session = _Session([
        _Resp(400, {"error": "invalid_params"}),
        _Resp(200, {"code": 40101, "msg": "no permission"}),
    ])
    pro = _client(session)

    with pytest.raises(TushareRestError) as exc:
        pro.query("stock_basic", list_status="L")

    assert len(session.calls) == 2
    assert "invalid_params" in str(exc.value)
    assert "no permission" in str(exc.value)


def test_has_more_pages_are_concatenated():
    def page(items, has_more):
        return _Resp(200, {"code": 0, "data": {"fields": ["ts_code"], "items": items, "has_more": has_more}})

    session = _Session([page([["a"], ["b"]], True), page([["c"]], False)])
    pro = _client(session)

    df = pro.adj_factor(trade_date="20260930")

    assert df["ts_code"].tolist() == ["a", "b", "c"]
    assert "offset" not in session.calls[0]["params"]
    assert session.calls[1]["params"]["offset"] == 2


def test_build_pro_prefers_rest_key(monkeypatch):
    monkeypatch.setenv("TUSHARE_API_KEY", "k-rest")
    monkeypatch.setenv("TUSHARE_REST_URL", "https://primary/tushare/pro")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    tushare_cn._pro_cache.clear()

    pro = tushare_cn._build_pro()

    assert isinstance(pro, TushareRestPro)
    assert pro.base_url == "https://primary/tushare/pro"
    assert tushare_cn.is_tushare_configured() is True
    assert tushare_cn._build_pro() is pro


def test_daily_bars_cover_latest_session_on_holiday(monkeypatch):
    sh = ZoneInfo("Asia/Shanghai")

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 7, 14, 0, tzinfo=sh)

    monkeypatch.setattr(tushare_cn, "datetime", _FrozenDatetime)
    monkeypatch.setattr(
        "app.services.market.ashare_session.latest_ashare_trading_day",
        lambda day: date(2026, 9, 30),
    )
    sep30 = int(datetime(2026, 9, 30, tzinfo=sh).timestamp())
    sep29 = int(datetime(2026, 9, 29, tzinfo=sh).timestamp())

    assert tushare_cn.daily_bars_cover_today([{"time": sep29}, {"time": sep30}]) is True
    assert tushare_cn.daily_bars_cover_today([{"time": sep29}]) is False
