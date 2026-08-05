from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.market.ashare_session import decide_ashare_daily_sync

SH = ZoneInfo("Asia/Shanghai")


def test_today_before_close_not_closed():
    d = decide_ashare_daily_sync(
        "2026-08-05",
        now=datetime(2026, 8, 5, 14, 0, tzinfo=SH),
        is_open_fn=lambda s: True,
    )
    assert d.ok is False
    assert d.reason == "not_closed"


def test_today_after_close_ok():
    d = decide_ashare_daily_sync(
        "2026-08-05",
        now=datetime(2026, 8, 5, 15, 5, tzinfo=SH),
        is_open_fn=lambda s: True,
    )
    assert d.ok is True
    assert d.reason == "ok"


def test_past_trading_day_ok():
    d = decide_ashare_daily_sync(
        "2026-08-04",
        now=datetime(2026, 8, 5, 10, 0, tzinfo=SH),
        is_open_fn=lambda s: s == "2026-08-04",
    )
    assert d.ok is True


def test_weekend_not_trading_day():
    d = decide_ashare_daily_sync(
        "2026-08-02",
        now=datetime(2026, 8, 5, 16, 0, tzinfo=SH),
        is_open_fn=lambda s: False,
    )
    assert d.ok is False
    assert d.reason == "not_trading_day"


def test_future_rejected():
    d = decide_ashare_daily_sync(
        "2026-08-06",
        now=datetime(2026, 8, 5, 16, 0, tzinfo=SH),
        is_open_fn=lambda s: True,
    )
    assert d.ok is False
    assert d.reason == "future"
