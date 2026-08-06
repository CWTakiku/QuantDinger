"""Tests for A-share PE-TTM percentile helper."""

from datetime import date, timedelta

import pandas as pd

from app.services.csi300_enhanced.pe_ttm import compute_pe_percentile


def test_compute_pe_percentile_from_series(monkeypatch):
    start = date(2025, 1, 1)
    idx = [start + timedelta(days=i) for i in range(10)]
    values = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0]
    series = pd.Series(values, index=pd.Index(idx))

    monkeypatch.setattr(
        "app.services.csi300_enhanced.pe_ttm._load_pe_series",
        lambda ts_code, s, e: series,
    )
    monkeypatch.setattr(
        "app.services.csi300_enhanced.pe_ttm._fetch_and_persist_pe_history",
        lambda *a, **k: 0,
    )

    out = compute_pe_percentile(
        symbol="CNStock:600176.SH",
        as_of=date(2025, 1, 10),
        lookback_days=30,
        autofetch=False,
    )
    assert out["pe_ttm"] == 28.0
    assert out["samples"] == 10
    assert abs(out["pe_percentile"] - 1.0) < 1e-9


def test_compute_pe_percentile_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.csi300_enhanced.pe_ttm._load_pe_series",
        lambda *a, **k: pd.Series(dtype=float),
    )
    monkeypatch.setattr(
        "app.services.csi300_enhanced.pe_ttm._fetch_and_persist_pe_history",
        lambda *a, **k: 0,
    )
    out = compute_pe_percentile(symbol="CNStock:600176.SH", as_of="2025-01-10", autofetch=True)
    assert out["pe_ttm"] is None
    assert out["pe_percentile"] is None
    assert out["samples"] == 0
