from datetime import date

import pandas as pd
import pytest

from app.services.ashare_data.rows import (
    BAR_COLUMNS,
    build_bar_rows,
    build_st_intervals,
    build_suspension_rows,
    check_day_completeness,
    st_codes_on,
)

DAY = date(2020, 1, 2)


def _frames(daily_codes, limit_codes, suspended=(), basic_codes=None):
    basic_codes = daily_codes if basic_codes is None else basic_codes
    return {
        "daily": pd.DataFrame(
            [
                {"ts_code": c, "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
                 "pre_close": 10.0, "vol": 1234.0, "amount": 1300.0}
                for c in daily_codes
            ]
        ),
        "stk_limit": pd.DataFrame([{"ts_code": c, "up_limit": 11.0, "down_limit": 9.0} for c in limit_codes]),
        "suspend_d": pd.DataFrame(
            [{"ts_code": c, "suspend_type": "S", "suspend_timing": None} for c in suspended],
            columns=["ts_code", "suspend_type", "suspend_timing"],
        ),
        "daily_basic": pd.DataFrame(
            [{"ts_code": c, "turnover_rate": 1.5, "total_mv": 200.0, "circ_mv": 100.0, "pe_ttm": 12.0}
             for c in basic_codes]
        ),
        "stock_st": pd.DataFrame(columns=["ts_code"]),
    }


def test_completeness_accepts_full_day_minus_suspensions():
    codes = [f"{i:06d}.SZ" for i in range(200)]
    ok, detail = check_day_completeness(_frames(codes[:190], codes, suspended=codes[190:]))
    assert ok, detail


@pytest.mark.parametrize(
    "frames, reason",
    [
        (_frames([], ["000001.SZ"]), "daily empty"),
        (_frames(["000001.SZ"], []), "stk_limit empty"),
        (
            _frames([f"{i:06d}.SZ" for i in range(90)], [f"{i:06d}.SZ" for i in range(100)],
                    basic_codes=[f"{i:06d}.SZ" for i in range(100)]),
            "daily coverage",
        ),
        (_frames(["000001.SZ"], ["000001.SZ"], basic_codes=[]), "daily_basic empty"),
        (
            _frames([f"{i:06d}.SZ" for i in range(100)], [f"{i:06d}.SZ" for i in range(100)],
                    basic_codes=[f"{i:06d}.SZ" for i in range(50)]),
            "daily_basic coverage",
        ),
    ],
)
def test_completeness_rejects_truncated_pulls(frames, reason):
    ok, detail = check_day_completeness(frames)
    assert not ok
    assert reason in detail


def test_halts_missing_from_suspend_d_do_not_fail_the_day():
    # 2015-07-10: ~1400 of 2783 listed stocks halted, suspend_d listed only 180.
    codes = [f"{i:06d}.SZ" for i in range(2783)]
    ok, detail = check_day_completeness(_frames(codes[:1425], codes, suspended=codes[-180:]))
    assert ok, detail
    assert "listed_minus_suspended=2603" in detail


def test_crossed_bar_fails_the_day():
    codes = [f"{i:06d}.SZ" for i in range(100)]
    frames = _frames(codes, codes)
    frames["daily_basic"]["close"] = 10.5
    frames["daily_basic"].loc[3, "close"] = 37.71
    frames["daily_basic"].loc[4, "close"] = 10.53
    ok, detail = check_day_completeness(frames)
    assert not ok
    assert "close mismatch 1: 000003.SZ" in detail


def test_bj_rows_missing_from_daily_basic_do_not_fail():
    codes = [f"{i:06d}.SZ" for i in range(100)]
    bj = [f"8{i:05d}.BJ" for i in range(10)]
    ok, detail = check_day_completeness(_frames(codes + bj, codes, basic_codes=codes))
    assert ok, detail


def test_bar_rows_convert_units_and_merge_limits():
    frames = _frames(["600000.SH"], ["600000.SH"])
    frames["stock_st"] = pd.DataFrame([{"ts_code": "600000.SH"}])
    (row,) = build_bar_rows(DAY, frames)
    bar = dict(zip(BAR_COLUMNS, row))
    assert bar["trade_date"] == DAY
    assert bar["volume"] == pytest.approx(123400.0)
    assert bar["amount"] == pytest.approx(1_300_000.0)
    assert bar["total_mv"] == pytest.approx(2_000_000.0)
    assert bar["up_limit"] == 11.0 and bar["down_limit"] == 9.0
    assert bar["is_st"] is True
    assert bar["pb"] is None


def test_suspension_rows_keep_timing():
    rows = build_suspension_rows(
        DAY, pd.DataFrame([{"ts_code": "000002.SZ", "suspend_type": "S", "suspend_timing": float("nan")}])
    )
    assert rows == [("000002.SZ", DAY, "S", "")]


def test_st_intervals_from_name_history():
    intervals = build_st_intervals(
        [
            {"ts_code": "000001.SZ", "name": "ST平安", "start_date": "20190101", "end_date": "20191231"},
            {"ts_code": "000001.SZ", "name": "平安银行", "start_date": "20200101", "end_date": None},
            {"ts_code": "000002.SZ", "name": "*ST万科", "start_date": "20191201", "end_date": ""},
        ]
    )
    assert st_codes_on(intervals, date(2019, 6, 1)) == {"000001.SZ"}
    assert st_codes_on(intervals, DAY) == {"000002.SZ"}
    assert st_codes_on(intervals, date(2018, 1, 1)) == set()
