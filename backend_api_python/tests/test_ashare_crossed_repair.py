from datetime import date

import pandas as pd

from app.services.ashare_data.sync import repair_crossed_daily

DAY = date(2026, 7, 27)


class _Pro:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, api, **params):
        self.calls.append((api, params))
        return pd.DataFrame(self.rows.get(params.get("ts_code"), []))


def test_crossed_rows_are_replaced_by_per_code_query():
    daily = pd.DataFrame(
        [
            {"ts_code": "000856.SZ", "close": 40.6, "vol": 1.0},
            {"ts_code": "600000.SH", "close": 10.0, "vol": 2.0},
        ]
    )
    basic = pd.DataFrame([{"ts_code": "000856.SZ", "close": 7.16}, {"ts_code": "600000.SH", "close": 10.0}])
    pro = _Pro({"000856.SZ": [{"ts_code": "000856.SZ", "close": 7.16, "vol": 3.0}]})
    fixed = repair_crossed_daily(pro, DAY, daily, basic).set_index("ts_code")
    assert fixed.loc["000856.SZ", "close"] == 7.16
    assert fixed.loc["600000.SH", "close"] == 10.0
    assert pro.calls == [("daily", {"ts_code": "000856.SZ", "trade_date": "20260727"})]


def test_unconfirmed_requery_keeps_original_row():
    daily = pd.DataFrame([{"ts_code": "000856.SZ", "close": 40.6}])
    basic = pd.DataFrame([{"ts_code": "000856.SZ", "close": 7.16}])
    pro = _Pro({"000856.SZ": [{"ts_code": "000856.SZ", "close": 40.6}]})
    assert repair_crossed_daily(pro, DAY, daily, basic)["close"].tolist() == [40.6]


def test_clean_day_makes_no_extra_queries():
    daily = pd.DataFrame([{"ts_code": "600000.SH", "close": 10.0}])
    basic = pd.DataFrame([{"ts_code": "600000.SH", "close": 10.02}])
    pro = _Pro({})
    repair_crossed_daily(pro, DAY, daily, basic)
    assert pro.calls == []
