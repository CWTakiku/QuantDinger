from app.services.csi300_enhanced.tushare_sync import (
    normalize_index_weight_frame,
    weights_to_platform_map,
)


def test_normalize_index_weight_frame():
    import pandas as pd

    raw = pd.DataFrame({
        "trade_date": ["20260730", "20260730"],
        "con_code": ["600519.SH", "000001.SZ"],
        "weight": [5.0, 3.0],
    })
    out = normalize_index_weight_frame(raw)
    assert list(out.columns) == ["trade_date", "con_code", "weight"]
    assert len(out) == 2


def test_weights_to_platform_map_normalizes():
    import pandas as pd

    frame = pd.DataFrame({
        "trade_date": ["20260730", "20260730"],
        "con_code": ["600519.SH", "000001.SZ"],
        "weight": [5.0, 5.0],
    })
    mapped = weights_to_platform_map(frame)
    assert mapped["CNStock:600519.SH"] == 0.5
    assert mapped["CNStock:000001.SZ"] == 0.5
    assert abs(sum(mapped.values()) - 1.0) < 1e-9
