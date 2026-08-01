from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services.csi300_enhanced.layered_alpha import (
    build_neutralized_factor,
    combine_layers,
    load_consensus_panel,
    load_flow_panel,
    load_industry_and_size,
)


def test_combine_layers_renormalizes_missing():
    mom = pd.Series({"A": 1.0, "B": -1.0})
    out = combine_layers(
        {"momentum": mom, "flow": pd.Series(dtype=float)},
        {"momentum": 0.2, "flow": 0.25, "value_quality": 0.2, "risk_liq": 0.15, "consensus": 0.2},
    )
    assert set(out.index) == {"A", "B"}
    assert abs(float(out.mean())) < 1e-9


def test_build_neutralized_factor_wires_preprocess():
    rng = np.random.default_rng(0)
    symbols = [f"s{i}" for i in range(40)]
    industry = {s: ("A" if i < 20 else "B") for i, s in enumerate(symbols)}
    log_mcap = pd.Series({s: float(i) for i, s in enumerate(symbols)})
    raw = pd.Series({s: log_mcap[s] + 0.01 * float(rng.normal()) for s in symbols})
    out = build_neutralized_factor(raw, industry, log_mcap)
    assert set(out.index) <= set(symbols)
    assert abs(float(out.mean())) < 1e-9
    corr = out.corr(log_mcap.reindex(out.index), method="pearson")
    assert abs(float(corr)) < 0.25


def test_load_industry_and_size_reads_db():
    rows_industry = [
        {"ts_code": "600519.SH", "industry": "白酒"},
        {"ts_code": "000001.SZ", "industry": "银行"},
    ]
    rows_size = [
        {"ts_code": "600519.SH", "circ_mv": 1000.0},
        {"ts_code": "000001.SZ", "circ_mv": 500.0},
    ]

    class _Cursor:
        def __init__(self):
            self._calls = 0

        def execute(self, sql, params=None):
            self._calls += 1
            self._sql = sql
            self._params = params

        def fetchall(self):
            if "industry_map" in self._sql:
                return rows_industry
            return rows_size

    class _Db:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    symbols = ["CNStock:600519.SH", "CNStock:000001.SZ"]
    with patch("app.services.csi300_enhanced.layered_alpha.get_db_connection", lambda: _Db()):
        industry, log_mcap = load_industry_and_size(symbols, date(2026, 7, 31))

    assert industry["CNStock:600519.SH"] == "白酒"
    assert industry["CNStock:000001.SZ"] == "银行"
    assert log_mcap["CNStock:600519.SH"] == np.log1p(1000.0)
    assert log_mcap["CNStock:000001.SZ"] == np.log1p(500.0)


def test_load_flow_panel_reads_db():
    rows_flow = [
        {"ts_code": "600519.SH", "north_net_buy": 120.0, "margin_balance": None},
        {"ts_code": "000001.SZ", "north_net_buy": None, "margin_balance": 800.0},
    ]

    class _Cursor:
        def execute(self, sql, params=None):
            self._sql = sql
            self._params = params

        def fetchall(self):
            return rows_flow

    class _Db:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    symbols = ["CNStock:600519.SH", "CNStock:000001.SZ"]
    with patch("app.services.csi300_enhanced.layered_alpha.get_db_connection", lambda: _Db()):
        flow = load_flow_panel(symbols, date(2026, 7, 31))

    assert flow["CNStock:600519.SH"] == 120.0
    assert flow["CNStock:000001.SZ"] == 800.0


def test_load_consensus_panel_reads_db():
    rows_consensus = [
        {"ts_code": "600519.SH", "eps_fy1": 50.0, "pe_fy1": 25.0, "rating_mean": 4.5},
        {"ts_code": "000001.SZ", "eps_fy1": None, "pe_fy1": 10.0, "rating_mean": 3.0},
    ]

    class _Cursor:
        def execute(self, sql, params=None):
            self._sql = sql
            self._params = params

        def fetchall(self):
            return rows_consensus

    class _Db:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    symbols = ["CNStock:600519.SH", "CNStock:000001.SZ"]
    with patch("app.services.csi300_enhanced.layered_alpha.get_db_connection", lambda: _Db()):
        consensus = load_consensus_panel(symbols, date(2026, 7, 31))

    assert consensus["CNStock:600519.SH"] == (50.0 + (1.0 / 25.0) + 4.5) / 3.0
    assert consensus["CNStock:000001.SZ"] == (1.0 / 10.0 + 3.0) / 2.0
