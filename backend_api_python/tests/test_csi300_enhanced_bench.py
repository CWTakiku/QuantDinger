from datetime import date
from unittest.mock import patch

from app.services.csi300_enhanced.bench import (
    get_csi300_bench_weight_source,
    get_csi300_bench_weights,
    get_csi300_bench_weights_with_meta,
)


def test_bench_weights_from_index_table_normalized():
    rows = [
        {"con_code": "600519.SH", "weight": 3.0},
        {"con_code": "000001.SZ", "weight": 1.0},
    ]
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=rows):
        out = get_csi300_bench_weights(date(2026, 7, 31))
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["CNStock:600519.SH"] == 0.75
    assert out["CNStock:000001.SZ"] == 0.25


def test_bench_weights_with_meta_exposes_pit_source():
    rows = [
        {"con_code": "600519.SH", "weight": 3.0},
        {"con_code": "000001.SZ", "weight": 1.0},
    ]
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=rows):
        weights, source = get_csi300_bench_weights_with_meta(date(2026, 7, 31))
        assert get_csi300_bench_weight_source(date(2026, 7, 31)) == "csi300_pit"
    assert source == "csi300_pit"
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_bench_weights_fallback_universe_member_weight_source():
    uni = {"CNStock:A": 2.0, "CNStock:B": 2.0}
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=[]), \
         patch("app.services.csi300_enhanced.bench._load_universe_member_weights", return_value=uni):
        weights, source = get_csi300_bench_weights_with_meta(
            date(2026, 7, 31), symbols=["CNStock:A", "CNStock:B"]
        )
    assert source == "universe_member_weight"
    assert weights == {"CNStock:A": 0.5, "CNStock:B": 0.5}


def test_bench_weights_fallback_equal_when_empty():
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=[]), \
         patch("app.services.csi300_enhanced.bench._load_universe_member_weights", return_value={}):
        out, source = get_csi300_bench_weights_with_meta(
            date(2026, 7, 31), symbols=["CNStock:A", "CNStock:B"]
        )
        plain = get_csi300_bench_weights(date(2026, 7, 31), symbols=["CNStock:A", "CNStock:B"])
    assert source == "equal_weight"
    assert out == {"CNStock:A": 0.5, "CNStock:B": 0.5}
    assert plain == out


def test_bench_pit_symbol_mismatch_does_not_use_universe_fallback():
    """PIT board exists but requested names miss it → equal_weight, not current universe."""
    rows = [
        {"con_code": "600519.SH", "weight": 3.0},
        {"con_code": "000001.SZ", "weight": 1.0},
    ]
    uni = {"CNStock:300308.SZ": 5.0}
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=rows), \
         patch("app.services.csi300_enhanced.bench._load_universe_member_weights", return_value=uni) as uni_mock:
        out, source = get_csi300_bench_weights_with_meta(
            date(2021, 8, 2),
            symbols=["CNStock:300308", "CNStock:300308.SZ"],
        )
    assert source == "equal_weight"
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert "CNStock:300308.SZ" in out
    uni_mock.assert_not_called()


def test_bench_aligns_bare_and_suffixed_cn_keys():
    rows = [
        {"con_code": "300308.SZ", "weight": 2.0},
        {"con_code": "600519.SH", "weight": 2.0},
    ]
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=rows):
        out = get_csi300_bench_weights(
            date(2026, 7, 31),
            symbols=["CNStock:300308", "CNStock:600519.SH"],
        )
    assert set(out) == {"CNStock:300308.SZ", "CNStock:600519.SH"}
    assert abs(out["CNStock:300308.SZ"] - 0.5) < 1e-9
