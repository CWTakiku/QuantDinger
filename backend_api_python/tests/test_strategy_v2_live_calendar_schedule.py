from datetime import date

import pandas as pd

from app.services.strategy_v2 import StrategyV2LiveSession

_CLOSED = {date(2026, 10, d) for d in range(1, 8)} | {date(2026, 9, 28)}


def _is_open(day: date) -> bool:
    return day.weekday() < 5 and day not in _CLOSED


def _frames():
    index = pd.date_range("2026-09-01", periods=5, freq="D")
    frame = pd.DataFrame({
        "open": [10.0] * 5,
        "high": [10.1] * 5,
        "low": [9.9] * 5,
        "close": [10.0] * 5,
        "volume": [1000] * 5,
    }, index=index)
    return {"CNStock:000001.SZ": frame}


_WEEKLY = """
def initialize(context):
    context.set_universe(["CNStock:000001.SZ"])
    context.subscribe(frequency="1d")
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    order_target_percent("000001.SZ", 0.5)
"""


def _session(code=_WEEKLY):
    frames = _frames()
    return StrategyV2LiveSession(
        code=code,
        frames=frames,
        initial_capital=100000,
        schedule_timezone="Asia/Shanghai",
        trading_day_fn=_is_open,
    ), frames


def test_weekly_skips_holiday_week_and_catches_up_same_day_once():
    session, frames = _session()

    holiday, _, _ = session.process(frames, schedule_time="2026-10-05 09:40:00+08:00")
    early, _, _ = session.process(frames, schedule_time="2026-10-12 09:30:00+08:00")
    late_start, _, _ = session.process(frames, schedule_time="2026-10-12 13:00:00+08:00")
    again, _, _ = session.process(frames, schedule_time="2026-10-12 14:00:00+08:00")
    tuesday, _, _ = session.process(frames, schedule_time="2026-10-13 09:40:00+08:00")

    assert holiday == []
    assert early == []
    assert len(late_start) == 1
    assert again == []
    assert tuesday == []


def test_weekly_defers_to_first_trading_day_when_monday_closed():
    session, frames = _session()

    monday, _, _ = session.process(frames, schedule_time="2026-09-28 10:00:00+08:00")
    tuesday, _, _ = session.process(frames, schedule_time="2026-09-29 10:00:00+08:00")
    wednesday, _, _ = session.process(frames, schedule_time="2026-09-30 10:00:00+08:00")

    assert monday == []
    assert len(tuesday) == 1
    assert wednesday == []


def test_fired_period_survives_restart():
    session, frames = _session()
    first, _, _ = session.process(frames, schedule_time="2026-10-12 09:35:00+08:00")
    restored, _ = _session()
    restored.restore_session_snapshot(session.session_snapshot())

    duplicate, _, _ = restored.process(frames, schedule_time="2026-10-12 10:30:00+08:00")

    assert len(first) == 1
    assert duplicate == []


def test_daily_schedule_skips_non_trading_days():
    code = _WEEKLY.replace('run_weekly(rebalance, weekday=1, time="09:35")', 'run_daily(rebalance, time="09:35")')
    session, frames = _session(code)

    saturday, _, _ = session.process(frames, schedule_time="2026-10-10 09:40:00+08:00")
    friday, _, _ = session.process(frames, schedule_time="2026-10-09 09:40:00+08:00")

    assert saturday == []
    assert len(friday) == 1


def test_order_for_symbol_without_data_is_skipped_in_live_session():
    code = _WEEKLY.replace(
        'order_target_percent("000001.SZ", 0.5)',
        'order_target_percent("000002.SZ", 0.2)\n    order_target_percent("000001.SZ", 0.5)',
    )
    session, frames = _session(code)

    orders, logs, _ = session.process(frames, schedule_time="2026-10-12 09:40:00+08:00")

    assert [o.symbol for o in orders] == ["CNStock:000001.SZ"]
    assert any("000002.SZ" in line and "skipped" in line for line in logs)
