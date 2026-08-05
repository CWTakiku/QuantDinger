"""Tests for External Alpha backtest coverage preflight."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.services.external_alpha.coverage import assert_external_alpha_score_coverage


CODE = """
def initialize(context):
    run_weekly(rebalance, weekday=1, time="09:35")

def rebalance(context, data):
    pass
"""


def test_skips_when_not_external_alpha():
    assert assert_external_alpha_score_coverage(
        code=CODE,
        params={},
        start_date=date(2025, 8, 5),
        end_date=date(2026, 6, 5),
    ) is None


def test_raises_when_panel_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.external_alpha.coverage.list_external_alpha_as_ofs",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.external_alpha.coverage.load_external_alpha_scores_as_of",
        lambda *args, **kwargs: pd.Series(dtype=float),
    )
    with pytest.raises(ValueError, match="无可读分数"):
        assert_external_alpha_score_coverage(
            code=CODE,
            params={"source": "rdagent", "version": "qm_smoke_loop7_model", "min_names": 10},
            start_date=date(2025, 8, 5),
            end_date=date(2026, 6, 5),
        )


def test_ok_when_some_rebalances_covered(monkeypatch):
    monkeypatch.setattr(
        "app.services.external_alpha.coverage.list_external_alpha_as_ofs",
        lambda **kwargs: ["2025-08-11"],
    )

    def fake_load(as_of, **kwargs):
        if str(as_of)[:10] >= "2025-08-10":
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.external_alpha.coverage.load_external_alpha_scores_as_of",
        fake_load,
    )
    out = assert_external_alpha_score_coverage(
        code=CODE,
        params={"source": "rdagent", "version": "v1", "min_names": 10, "score_lag_days": 0},
        start_date=date(2025, 8, 5),
        end_date=date(2025, 8, 25),
    )
    assert out is not None
    assert out["covered"] > 0
