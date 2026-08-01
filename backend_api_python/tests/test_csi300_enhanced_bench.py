from datetime import date
from unittest.mock import patch

from app.services.csi300_enhanced.bench import get_csi300_bench_weights


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


def test_bench_weights_fallback_equal_when_empty():
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=[]), \
         patch("app.services.csi300_enhanced.bench._load_universe_member_weights", return_value={}):
        out = get_csi300_bench_weights(date(2026, 7, 31), symbols=["CNStock:A", "CNStock:B"])
    assert out == {"CNStock:A": 0.5, "CNStock:B": 0.5}
